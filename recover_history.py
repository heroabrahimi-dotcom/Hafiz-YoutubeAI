"""Recover historic visual SCRIPT SENTENCE fields from user's authenticated Chrome session.
No external credentials or authentication tokens are read/stored.
"""
import json, re, time
from pathlib import Path


def parse_conversation(data):
    messages=[]
    mapping=data.get('mapping',{})
    if isinstance(mapping,dict):
        for record in mapping.values():
            if not isinstance(record,dict): continue
            m=record.get('message') or {}
            if (m.get('author') or {}).get('role') != 'assistant': continue
            content=m.get('content') or {}
            parts=content.get('parts') or []
            text='\n'.join(x for x in parts if isinstance(x,str))
            if text and re.search(r'VISUAL\s+\d+',text,re.I): messages.append(text)
    if not messages:
        for r in data.get('messages',[]) if isinstance(data.get('messages'),list) else []:
            if not isinstance(r,dict):continue
            m=r.get('message') or r
            if (m.get('author') or {}).get('role') != 'assistant':continue
            text='\n'.join(x for x in (m.get('content') or {}).get('parts',[]) if isinstance(x,str))
            if text and re.search(r'VISUAL\s+\d+',text,re.I): messages.append(text)
    return messages


def recover(url,cdp,verbose=True):
    from playwright.sync_api import sync_playwright
    conversation_id=url.rstrip('/').split('/')[-1].split('?')[0]
    with sync_playwright() as p:
        browser=p.chromium.connect_over_cdp(cdp,timeout=25000)
        try:
            matches=[pg for ctx in browser.contexts for pg in ctx.pages if 'chatgpt.com' in pg.url and conversation_id in pg.url]
            if not matches: raise RuntimeError('Original conversation tab not found in Chrome. Open it before running.')
            page=matches[0]
            response=page.evaluate('''async (id) => {
              try {
                const response=await fetch('/backend-api/conversation/'+encodeURIComponent(id), {credentials:'include', headers:{'Accept':'application/json'}});
                if(!response.ok)return {ok:false,status:response.status};
                const result=await response.json();
                return {ok:true,data:result};
              } catch(e) {return {ok:false,error:String(e)};}
            }''',conversation_id)
            if response.get('ok'):
                data=response.get('data') or {}
                messages=parse_conversation(data)
                if verbose: print(f'Conversation-history recovery: {len(messages)} visual message blocks.',flush=True)
                if messages:return messages
            if verbose: print(f'Conversation-history endpoint unavailable ({response.get("status",response.get("error","empty"))}); trying full-page navigation.',flush=True)
            # DOM fallback: walk Chrome's page upward and capture the rendered text at each step
            found={}
            page.keyboard.press('Control+Home')
            for step in range(150):
                capture=page.evaluate('''() => {
                  const blocks=[];
                  for(const e of document.querySelectorAll('[data-message-author-role="assistant"], [data-testid^="conversation-turn-"]')) {
                    const t=(e.innerText||'').trim();
                    if(/VISUAL\\s+\\d+/i.test(t)) blocks.push(t);
                  }
                  for(const h of document.querySelectorAll('h4')) {
                    if((h.innerText||'').trim()!=='ChatGPT said:')continue;
                    let node=h.parentElement;
                    for(let i=0;i<8&&node;i++,node=node.parentElement){
                      let t=(node.innerText||'').trim();
                      if(t.startsWith('ChatGPT said:') && /VISUAL\\s+\\d+/i.test(t)){blocks.push(t);break;}
                    }
                  }
                  const scrollables=[...document.querySelectorAll('*')].filter(e=>e.scrollHeight>e.clientHeight+150 && e.clientHeight>200 && /scroll|auto/.test(getComputedStyle(e).overflowY));
                  const el=scrollables.sort((a,b)=>b.scrollHeight-a.scrollHeight)[0];
                  return {blocks,scrollTop:el?el.scrollTop:window.scrollY,scrollHeight:el?el.scrollHeight:document.documentElement.scrollHeight};
                }''')
                for t in capture['blocks']:
                    found[t]=t
                # check if enough scenes discovered without importing module recursively
                from sync_video import extract_scenes
                if len(extract_scenes(list(found.values())))>=56:break
                page.mouse.wheel(0, -1800)
                page.keyboard.press('PageUp')
                if step%10==0 and verbose:print(f'Page history scan: {len(extract_scenes(list(found.values())))} numbered visuals recovered.',flush=True)
                page.wait_for_timeout(100)
            return list(found.values())
        finally:
            browser.close()
