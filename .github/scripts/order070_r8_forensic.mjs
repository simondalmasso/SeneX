import fs from 'node:fs';
import { ApiClient, ApiClientInMemoryContextProvider } from '@northflank/js-client';

const token = process.env.NORTHFLANK_API_TOKEN;
if (!token) throw new Error('NORTHFLANK_API_TOKEN_MISSING');
const contextProvider = new ApiClientInMemoryContextProvider();
await contextProvider.addContext({name:'r8-forensic', token});
const apiClient = new ApiClient(contextProvider, {throwErrorOnHttpErrorCode:true});
const command = String.raw`set -eu
printf 'TS='; date -u +%Y-%m-%dT%H:%M:%SZ
printf 'HOST='; hostname
printf 'CGROUP_CURRENT='; cat /sys/fs/cgroup/memory.current 2>/dev/null || true
printf 'CGROUP_MAX='; cat /sys/fs/cgroup/memory.max 2>/dev/null || true
printf '%s\n' '---MEMORY_STAT---'
grep -E '^(anon|file|kernel|kernel_stack|pagetables|percpu|sock|shmem|file_mapped|file_dirty|file_writeback|swapcached|slab|slab_reclaimable|slab_unreclaimable) ' /sys/fs/cgroup/memory.stat 2>/dev/null || true
printf '%s\n' '---PROCFS_PYTHON---'
python - <<'PY'
import glob, os
keys=('VmRSS','RssAnon','RssFile','VmHWM','VmSize','Threads')
for d in sorted(glob.glob('/proc/[0-9]*'), key=lambda p:int(p.rsplit('/',1)[-1])):
    try:
        pid=d.rsplit('/',1)[-1]
        raw=open(d+'/status').read().splitlines()
        st={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in raw if ':' in line}
        cmd=open(d+'/cmdline','rb').read().replace(b'\\0',b' ').decode(errors='replace')[:220]
        if not cmd: continue
        print('PROC', 'pid='+pid, 'name='+st.get('Name','?'), *(k+'='+st.get(k,'?') for k in keys), 'cmd='+cmd)
        if 'uvicorn' in cmd:
            print('UVICORN_PID='+pid)
            try:
                sm=open(d+'/smaps_rollup').read().splitlines()
                for line in sm:
                    if line.startswith(('Rss:','Pss:','Pss_Anon:','Pss_File:','Private_Dirty:','Anonymous:','Swap:')): print('UVICORN_'+line)
            except Exception as e: print('UVICORN_SMAPS_ERROR='+type(e).__name__)
    except Exception:
        pass
PY`;
const samples=[];
const started=new Date().toISOString();
try {
  const r=await apiClient.exec.execServiceCommand(
    {projectId:'seneciobot', serviceId:'senecio-h011'},
    {command:['sh','-c',command]}
  );
  samples.push({sample:1, started, exitCode:r?.commandResult?.exitCode, status:r?.commandResult?.status, stdout:r?.stdOut || '', stderr:r?.stdErr || ''});
} catch (e) {
  samples.push({sample:1, started, error:String(e?.message || e), name:e?.name || null});
}
fs.mkdirSync('order070-r8-forensic-evidence',{recursive:true});
fs.writeFileSync('order070-r8-forensic-evidence/container_exec_samples.json',JSON.stringify({captured_at:new Date().toISOString(),samples},null,2)+'\n');
console.log(JSON.stringify({samples:samples.length, exec_ok:samples.some(x=>x.status==='Success' || x.exitCode===0), errors:samples.filter(x=>x.error).map(x=>x.error)},null,2));
