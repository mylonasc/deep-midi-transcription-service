import { useCallback, useEffect, useState } from "react";
import { api, type Job } from "./api";

/** Polls the job list every 2s until unmounted. Returns jobs + refresh. */
export function useJobPolling() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [connectionError, setConnectionError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try { setJobs(await api.list()); setConnectionError(null); }
    catch (reason) { setConnectionError(String(reason)); }
    finally { setLoaded(true); }
  }, []);

  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      await refresh();
      if (!disposed) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); };
  }, [refresh]);

  return { jobs, loaded, connectionError, refresh };
}
