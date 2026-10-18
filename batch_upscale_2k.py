"""Safe local AI superresolution for completed Flow image batches.

Never changes Flow state. Back up originals, stage output, and reject suspicious
AI results. Already-2K files are also AI processed once, with exact source backup.
"""
from pathlib import Path
from PIL import Image, ImageOps, ImageStat, ImageChops
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile

TARGET = (2560, 1440)
EXE = Path(r'C:\RealESRGAN\realesrgan-ncnn-vulkan.exe')
MODELS = EXE.parent / 'models'
MODEL = 'realesrgan-x4plus-anime'
EXTS = ('.png', '.jpg', '.jpeg', '.webp')
MARKER = '_ai_2k_verified_v2_png'

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            h.update(chunk)
    return h.hexdigest()

def get_size(path):
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        return im.size

def find_original(source, originals):
    matches = [p for p in originals.glob(source.stem + '.*')
               if p.is_file() and p.suffix.lower() in EXTS]
    if len(matches) > 1:
        raise RuntimeError(f'Multiple originals for {source.name}: {matches}; resolve manually.')
    return matches[0] if matches else None

def _output_quality(original, output):
    """Detect gross tile scrambling by comparing low-frequency composition."""
    with Image.open(original) as old, Image.open(output) as new:
        baseline = ImageOps.fit(ImageOps.exif_transpose(old).convert('RGB'), (320,180), Image.Resampling.LANCZOS)
        candidate = new.convert('RGB').resize((320,180), Image.Resampling.LANCZOS)
        diff = ImageChops.difference(baseline, candidate)
        mae = sum(ImageStat.Stat(diff).mean)/3
    # This detects very obvious rearrangements, not every possible visual defect.
    if mae > 32:
        raise RuntimeError(f'AI image looks substantially different from the original (pixel difference {mae:.1f}); NOT replacing it.')
    return mae

def ai_upscale(source, originals, replace=False):
    source = Path(source)
    original = find_original(source, originals)
    size = get_size(source)
    if original is None:
        # A 2560x1440 file may be merely resized, not AI-enhanced. Run real AI
        # on it too, preserving those source bytes before an atomic replacement.
        original = source
    elif original.resolve() == source.resolve():
        raise RuntimeError('Original and output paths must differ.')
    input_size = get_size(original)
    if not 1.72 <= input_size[0]/input_size[1] <= 1.84:
        raise RuntimeError(f'Unexpected aspect ratio: {original} {input_size}')
    marker = originals / (source.stem + MARKER + '.json')
    if replace and marker.exists():
        state = json.loads(marker.read_text('utf-8'))
        if state.get('original_sha256') == sha(original) and state.get('output_sha256') == sha(source):
            print(f'AI 2K SKIP: verified previously {source.name}', flush=True)
            return source
    print(f'AI ESRGAN INPUT: {original} ({input_size[0]}x{input_size[1]}); model={MODEL}, tile=128, GPU=0', flush=True)
    if not EXE.is_file() or not (MODELS/(MODEL+'.param')).is_file() or not (MODELS/(MODEL+'.bin')).is_file():
        raise RuntimeError(f'Real-ESRGAN executable/model not found: {EXE}, {MODELS}/{MODEL}.*')
    with tempfile.TemporaryDirectory(prefix='ai2k_',dir=str(source.parent)) as temp:
        temp=Path(temp)
        input_copy=temp/'input.png'
        with Image.open(original) as img:
            ImageOps.exif_transpose(img).convert('RGB').save(input_copy)
        intermediate=temp/'ai.png'
        command=[str(EXE),'-i',str(input_copy),'-o',str(intermediate),'-m',str(MODELS),
                 '-n',MODEL,'-s','4','-t','128','-g','0','-j','1:1:1','-f','png']
        run=subprocess.run(command,capture_output=True,text=True,errors='replace',timeout=900,cwd=str(EXE.parent))
        if run.returncode != 0 or not intermediate.is_file():
            raise RuntimeError(f'Real-ESRGAN failed: {(run.stderr or run.stdout)[-800:]}')
        get_size(intermediate)
        with Image.open(intermediate) as image:
            converted=ImageOps.fit(image.convert('RGB'),TARGET,Image.Resampling.LANCZOS)
            preview=temp/'result.png'
            converted.save(preview,format='PNG')
        if get_size(preview) != TARGET or preview.stat().st_size < 10000:
            raise RuntimeError('Upscaled image failed dimension/file-size check.')
        difference=_output_quality(original,preview)
        if not replace:
            test=source.parent/(source.stem+'_AI_TEST.png')
            if test.exists():
                raise RuntimeError(f'Test file exists: {test}; refusing to overwrite.')
            shutil.copy2(preview,test)
            print(f'TEST READY: {test} (composition difference {difference:.1f}); original NOT modified',flush=True)
            return test
        originals.mkdir(parents=True,exist_ok=True)
        archive=find_original(source,originals)
        if archive is None:
            archive=originals/source.name
            shutil.copy2(source,archive)
            original=archive
        # Archive the existing (possibly damaged) output before replacement.
        if sha(source) != sha(original):
            prev=source.parent/'_previous_bad_2k'
            prev.mkdir(parents=True,exist_ok=True)
            old=prev/source.name
            if not old.exists(): shutil.copy2(source,old)
        # Preserve original filename in _original_before_2k; publish AI result as lossless PNG.
        output = source.with_suffix('.png')
        staged = temp / output.name
        shutil.copy2(preview, staged)
        get_size(staged)
        # Do not overwrite another output without an archive of it.
        if output != source and output.exists():
            raise RuntimeError(f'AI 2K conflict: {output} already exists; original unchanged.')
        if output != source:
            old_dir=source.parent/'_previous_jpg_before_png_2k'
            old_dir.mkdir(parents=True,exist_ok=True)
            previous=old_dir/source.name
            if not previous.exists(): shutil.copy2(source,previous)
        os.replace(staged,output)
        # Journal must follow the new extension, or resume would think the image was missing.
        state_path = source.parent.parent/'FlowPromptAgentData'/'state.json'
        if state_path.is_file():
            data=json.loads(state_path.read_text(encoding='utf-8-sig'))
            number=str(int(source.stem.split('_')[-1]))
            row=data.get('items',{}).get(number)
            if isinstance(row,dict) and row.get('status')=='completed':
                old_file=Path(row.get('file','')) if row.get('file') else None
                if old_file and old_file.resolve()==source.resolve():
                    row['file']=str(output)
                    row['width'],row['height']=TARGET
                    tmp=state_path.with_name('state.json.ai2k.tmp')
                    tmp.write_text(json.dumps(data,indent=2,ensure_ascii=False),encoding='utf-8')
                    os.replace(tmp,state_path)
        marker.write_text(json.dumps({'original_sha256':sha(original),'output_sha256':sha(output),
                                     'size':TARGET,'model':MODEL,'tile':128,'difference':difference}),encoding='utf-8')
        if output != source:
            source.unlink()
        print(f'AI 2K PNG COMPLETE: {output.name} ({output.stat().st_size/1048576:.2f} MiB); original: {original}',flush=True)
        return output

def ensure_batch_2k(project, rows):
    folder=Path(project.folder)/'images'
    originals=folder/'_original_before_2k'
    items=[]
    for row in rows:
        n=int(row['number'])
        found=[p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS
               and p.stem in (f'visual_{n:03d}',f'visual_{n}')]
        if len(found)!=1: raise RuntimeError(f'2K Visual {n}: expected exactly one downloaded file.')
        items.append(found[0])
    for item in items:
        # Resolution alone does not prove AI upscaling. Process already-2K
        # images once too; the SHA marker prevents repeat conversions.
        ai_upscale(item,originals,replace=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--images',type=Path,required=True)
    parser.add_argument('--visual',type=int,default=1)
    parser.add_argument('--apply',action='store_true',help='After inspecting TEST image, permit replacement for only this visual')
    args=parser.parse_args()
    folder=args.images
    candidates=[p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXTS and
                p.stem in (f'visual_{args.visual:03d}',f'visual_{args.visual}')]
    if len(candidates)!=1:
        raise RuntimeError(f'Cannot locate exactly one visual_{args.visual:03d} image in {folder}')
    ai_upscale(candidates[0],folder/'_original_before_2k',replace=args.apply)

if __name__=='__main__': main()
