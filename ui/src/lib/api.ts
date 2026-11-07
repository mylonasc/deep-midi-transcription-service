export const API_URL = import.meta.env.VITE_API_URL ?? "/api";

export type JobStatus =
  | "queued" | "downloading" | "downloaded" | "transcribing"
  | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  youtube_url: string;
  status: JobStatus;
  config: {
    yt_dlp: { max_duration_s: number; allow_mix: boolean };
    transcription: { model: string };
  };
  progress: { download_progress: number | null; chunks_completed: number | null; chunks_total: number | null };
  title: string | null;
  duration_s: number | null;
  error: string | null;
  audio_key: string | null;
  midi_key: string | null;
  events_key: string | null;
  retry_count: number;
}

export interface ModelAvailability {
  default_model: string;
  models: { size: string; status: "available" | "download_required" | "unavailable";
    source: "configured" | "huggingface_cache"; message: string }[];
  scope: string;
}

async function req(path: string, init?: RequestInit) {
  const r = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!r.ok) {
    const text = await r.text();
    let message = text;
    try { const body = JSON.parse(text); message = typeof body.detail === "string" ? body.detail : text; } catch { /* Non-JSON proxy error. */ }
    throw new Error(message || `Request failed (${r.status})`);
  }
  if (r.status === 204) return null;
  return r.json();
}

export const api = {
  modelHealth: (): Promise<ModelAvailability> => req("/health/models"),
  list: async (): Promise<Job[]> => {
    const jobs: Job[] = [];
    for (let offset = 0; ; offset += 200) {
      const page: Job[] = await req(`/jobs?limit=200&offset=${offset}`);
      jobs.push(...page);
      if (page.length < 200) return jobs;
    }
  },
  create: (youtube_url: string, model: string, max_duration_s: number, allow_mix: boolean) =>
    req("/jobs", {
      method: "POST",
      body: JSON.stringify({
        youtube_url,
        config: {
          version: 1,
          yt_dlp: { max_duration_s, allow_mix },
          transcription: { model },
          extensions: {},
        },
      }),
    }),
  cancel: (id: string): Promise<Job> => req(`/jobs/${id}/cancel`, { method: "POST" }),
  retry: (id: string): Promise<Job> => req(`/jobs/${id}/retry`, { method: "POST" }),
  remove: (id: string) => req(`/jobs/${id}`, { method: "DELETE" }),
  midiUrl: (id: string) => `${API_URL}/jobs/${id}/midi`,
  audioUrl: (id: string) => `${API_URL}/jobs/${id}/audio`,
};
