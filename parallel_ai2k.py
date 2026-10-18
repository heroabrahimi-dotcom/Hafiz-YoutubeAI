"""One background Real-ESRGAN worker, started by the parent pipeline.
Flow runs only in the main thread. Worker never edits Flow journal or source images.
Each batch has an on-disk manifest and is recoverable after interrupted runs.
"""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from PIL import Image
from batch_upscale_2k import ai_upscale, MARKER

EXTS={'.png','.jpg','.jpeg','.webp'}

def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def atomic(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(data,indent=2),encoding='utf-8')
    os.replace(tmp,path)

def image_size(p):
    with Image.open(p) as im:im.verify()
    with Image.open(p) as im:return im.size

class ParallelAI2K:
    def __init__(self,project):
        self.folder=Path(project.folder)
        self.images=self.folder/'images'
        self.state=self.folder/'FlowPromptAgentData'/'parallel_ai2k'
        self.state.mkdir(parents=True,exist_ok=True)
        self.pool=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='RealESRGAN')
        self.jobs=[]

    def enqueue(self,rows):
        nums=[int(r['number']) for r in rows]
        if not nums:return
        name=f'{nums[0]:03d}-{nums[-1]:03d}'
        manifest=self.state/(name+'.json')
        # Preserve a previously completed batch manifest. Worker re-verifies it.
        if not manifest.exists():atomic(manifest,{'numbers':nums,'status':'queued','images':{}})
        print(f'PARALLEL AI2K QUEUED: {name}; Flow may start next batch immediately.',flush=True)
        self.jobs.append((name,self.pool.submit(self._run_batch,nums,manifest)))

    def _choose_input(self,num):
        stem=f'visual_{num:03d}'
        matches=[p for p in self.images.iterdir() if p.is_file() and p.stem in (stem,f'visual_{num}') and p.suffix.lower() in EXTS]
        # A raw JPG and an already generated PNG can coexist during overlap.
        if len(matches)>2 or (len(matches)==2 and {p.suffix.lower() for p in matches}!={'.jpg','.png'}):
            raise RuntimeError(f'VISUAL {num}: ambiguous image sources: {matches}')
        if not matches:raise RuntimeError(f'VISUAL {num}: no downloaded source image')
        raw=next((p for p in matches if p.suffix.lower()!='.png'),None)
        return raw or matches[0]

    def _run_batch(self,nums,manifest):
        data=json.loads(manifest.read_text('utf-8-sig'))
        data['status']='running';atomic(manifest,data)
        for n in nums:
            key=str(n);src=self._choose_input(n)
            final=self.images/f'visual_{n:03d}.png'
            # Skip ONLY when a cryptographically recorded Real-ESRGAN output exists.
            old=data.setdefault('images',{}).get(key,{})
            if old.get('status')=='done' and final.is_file() and digest(final)==old.get('sha256') and image_size(final)==(2560,1440):
                print(f'PARALLEL AI2K SKIP VERIFIED: VISUAL {n}',flush=True)
                continue
            # Previously produced PNG from the normal AI worker is already verified by its AI marker.
            marker=self.images/'_original_before_2k'/(f'visual_{n:03d}'+MARKER+'.json')
            if src.suffix.lower()=='.png' and marker.is_file():
                try:
                    record=json.loads(marker.read_text('utf-8-sig'))
                    if record.get('output_sha256')==digest(src) and image_size(src)==(2560,1440):
                        data['images'][key]={'status':'done','sha256':digest(src),'source':'prior_verified_ai2k'}
                        atomic(manifest,data)
                        continue
                except (ValueError,KeyError):pass
            if final.exists() and final!=src:
                raise RuntimeError(f'VISUAL {n}: existing PNG lacks verified AI marker; refusing overwrite: {final}')
            # Upscaler writes only to an isolated temporary directory. Original source
            # and Flow journal remain untouched while next Flow batch runs.
            with tempfile.TemporaryDirectory(prefix=f'parallel_ai2k_{n:03d}_',dir=str(self.state)) as tmp:
                work=Path(tmp)
                staged_input=work/src.name
                shutil.copy2(src,staged_input)
                out=ai_upscale(staged_input,work/'_original_before_2k',replace=True)
                if image_size(out)!=(2560,1440):raise RuntimeError(f'VISUAL {n}: wrong Real-ESRGAN dimensions')
                if out.stat().st_size<10000:raise RuntimeError(f'VISUAL {n}: suspiciously small output')
                originals=self.images/'_original_before_2k'
                originals.mkdir(exist_ok=True)
                archive=originals/src.name
                if not archive.exists():shutil.copy2(src,archive)
                elif digest(archive)!=digest(src):
                    # A recovered source may be different from historic untouched source.
                    # Only permit this when the original is already archived and AI source is the same.
                    raise RuntimeError(f'VISUAL {n}: backup differs from local download; stopped without overwrite')
                stage=self.images/(f'.visual_{n:03d}.parallel.partial.png')
                shutil.copy2(out,stage)
                image_size(stage)
                os.replace(stage,final)
            data['images'][key]={'status':'done','sha256':digest(final),'source_sha256':digest(src),'model':'realesrgan-x4plus-anime'}
            atomic(manifest,data)
            print(f'PARALLEL AI2K DONE: VISUAL {n} -> {final.name} ({final.stat().st_size/1048576:.2f} MiB)',flush=True)
        data['status']='done';atomic(manifest,data)
        print(f'PARALLEL AI2K BATCH COMPLETE: {nums[0]}-{nums[-1]}',flush=True)

    def finish(self):
        # Blocking wait happens ONLY after all Flow batches have downloaded.
        try:
            for name,future in self.jobs:
                print(f'PARALLEL AI2K FINAL GATE: checking {name}',flush=True)
                future.result()  # propagate a failed batch; do not render
            # Flow is no longer writing its journal; safely publish PNG paths.
            journal=self.folder/'FlowPromptAgentData'/'state.json'
            if journal.is_file():
                data=json.loads(journal.read_text('utf-8-sig'))
                for manifest in self.state.glob('*.json'):
                    rec=json.loads(manifest.read_text('utf-8-sig'))
                    if rec.get('status')!='done':continue
                    for key,meta in rec.get('images',{}).items():
                        if meta.get('status')!='done':continue
                        png=self.images/f'visual_{int(key):03d}.png'
                        if not png.is_file() or digest(png)!=meta['sha256']:
                            raise RuntimeError(f'VISUAL {key}: AI output missing or changed at final gate')
                        row=data.get('items',{}).get(str(int(key)))
                        if not row or row.get('status')!='completed':
                            raise RuntimeError(f'VISUAL {key}: original Flow download not verified in journal')
                        row['file']=str(png);row['width']=2560;row['height']=1440
                atomic(journal,data)
            # Remove duplicate working JPGs only AFTER Flow journal is fully updated.
            for name,future in self.jobs:
                for n in (json.loads((self.state/(name+'.json')).read_text('utf-8-sig'))['numbers']):
                    png=self.images/f'visual_{n:03d}.png'
                    for ext in ('.jpg','.jpeg','.webp'):
                        jpg=self.images/f'visual_{n:03d}{ext}'
                        if jpg.exists() and png.exists():
                            jpg.unlink()  # pristine copy already in _original_before_2k
            print('PARALLEL AI2K FINAL GATE PASSED: all queued images are verified 2K PNG.',flush=True)
        finally:
            self.pool.shutdown(wait=True,cancel_futures=False)

    def shutdown(self):
        # Ensure no worker is left updating images after the main pipeline exits.
        self.pool.shutdown(wait=True,cancel_futures=False)
