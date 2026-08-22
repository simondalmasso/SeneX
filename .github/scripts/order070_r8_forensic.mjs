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
printf '%s\n' '---PROCESSES---'
uvpid=''
for d in /proc/[0-9]*; do
  [ -r "$d/status" ] || continue
  pid="\${d#/proc/}"
  name="$(sed -n 's/^Name:[[:space:]]*//p' "$d/status" 2>/dev/null | head -n1)"
  rss="$(sed -n 's/^VmRSS:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$d/status" 2>/dev/null | head -n1)"
  anon="$(sed -n 's/^RssAnon:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$d/status" 2>/dev/null | head -n1)"
  cmd="$(tr '\000' ' ' < "$d/cmdline" 2>/dev/null | cut -c1-180 || true)"
  printf 'PROC pid=%s name=%s rss_kb=%s anon_kb=%s cmd=%s\n' "$pid" "$name" "\${rss:-0}" "\${anon:-0}" "$cmd"
  case "$cmd" in *uvicorn*) [ -n "$uvpid" ] || uvpid="$pid";; esac
done
printf 'UVICORN_PID=%s\n' "$uvpid"
if [ -n "$uvpid" ]; then
  printf '%s\n' '---STATUS---'
  grep -E '^(Name|Pid|PPid|VmPeak|VmSize|VmHWM|VmRSS|RssAnon|RssFile|RssShmem|Threads):' "/proc/$uvpid/status" 2>/dev/null || true
  printf '%s\n' '---SMAPS_ROLLUP---'
  grep -E '^(Rss|Pss|Pss_Anon|Pss_File|Pss_Shmem|Shared_Clean|Shared_Dirty|Private_Clean|Private_Dirty|Anonymous|LazyFree|AnonHugePages|Swap):' "/proc/$uvpid/smaps_rollup" 2>/dev/null || true
  printf '%s\n' '---LOADED_NATIVE_LIBS---'
  awk '{print $NF}' "/proc/$uvpid/maps" 2>/dev/null | grep -E '/(site-packages|lib)/.*\.so' | sort -u | tail -n 120 || true
fi`;
const samples=[];
for (let i=0;i<1;i++) {
  const started=new Date().toISOString();
  try {
    const r=await apiClient.exec.execServiceCommand(
      {projectId:'seneciobot', serviceId:'senecio-h011'},
      {command:['sh','-c',command]}
    );
    samples.push({sample:i+1, started, exitCode:r?.commandResult?.exitCode, status:r?.commandResult?.status, stdout:r?.stdOut || '', stderr:r?.stdErr || ''});
  } catch (e) {
    samples.push({sample:i+1, started, error:String(e?.message || e), name:e?.name || null});
  }
}
fs.mkdirSync('order070-r8-forensic-evidence',{recursive:true});
fs.writeFileSync('order070-r8-forensic-evidence/container_exec_samples.json',JSON.stringify({captured_at:new Date().toISOString(),samples},null,2)+'\n');
console.log(JSON.stringify({samples:samples.length, exec_ok:samples.some(x=>x.status==='Success' || x.exitCode===0), errors:samples.filter(x=>x.error).map(x=>x.error)},null,2));
