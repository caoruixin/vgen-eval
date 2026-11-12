import { useEffect, useRef, useState } from "react";
import { useI18n } from "../i18n";

export default function SyncBar({ videos, duration, audioId, setAudioId, audioOptions, loop, setLoop }: { videos: () => HTMLVideoElement[]; duration: number; audioId: string; setAudioId: (id: string) => void; audioOptions: { id: string; label: string }[]; loop: boolean; setLoop: (l: boolean) => void }) {
  const { t } = useI18n();
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const loopRef = useRef(loop);
  loopRef.current = loop;
  const groupPlaying = useRef(false);
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const vs = videos();
      // the longest clip is the clock; shorter clips hold their last frame until the group restarts
      const master = vs.reduce<HTMLVideoElement | undefined>((a, v) => (!a || (v.duration || 0) > (a.duration || 0) ? v : a), undefined);
      if (master) setTime(master.currentTime);
      const anyPlaying = vs.some((v) => !v.paused && !v.ended);
      if (master && groupPlaying.current && loopRef.current && master.ended) {
        vs.forEach((v) => {
          v.currentTime = 0;
          void v.play().catch(() => undefined);
        });
      } else if (groupPlaying.current && !anyPlaying && !loopRef.current) {
        groupPlaying.current = false;
      }
      setPlaying(anyPlaying || (groupPlaying.current && loopRef.current));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [videos]);
  const seek = (s: number) => videos().forEach((v) => (v.currentTime = Math.min(s, Math.max(0, (v.duration || s) - 0.05))));
  const playAll = () => {
    const vs = videos();
    const master = vs.reduce<HTMLVideoElement | undefined>((a, v) => (!a || (v.duration || 0) > (a.duration || 0) ? v : a), undefined);
    const at = master && !master.ended ? master.currentTime : 0;
    groupPlaying.current = true;
    vs.forEach((v) => {
      v.currentTime = Math.min(at, Math.max(0, (v.duration || at) - 0.05));
      void v.play().catch(() => undefined);
    });
  };
  const pauseAll = () => {
    groupPlaying.current = false;
    videos().forEach((v) => v.pause());
  };
  return (
    <div className="card sticky top-[52px] z-20 flex flex-wrap items-center gap-3 px-3 py-2">
      <button className="btn btn-primary" onClick={() => (playing ? pauseAll() : playAll())}>
        {playing ? `❚❚ ${t("board.pause")}` : `▶ ${t("board.play")}`}
      </button>
      <button className="btn" onClick={() => { seek(0); }}>⏮</button>
      <input type="range" min={0} max={Math.max(1, duration)} step={0.04} value={Math.min(time, duration)} onChange={(e) => seek(Number(e.target.value))} className="min-w-[200px] flex-1" aria-label="scrub" />
      <span className="tnum w-24 text-xs text-ink-2">
        {time.toFixed(1)}s / {duration.toFixed(1)}s
      </span>
      <label className="flex items-center gap-1.5 text-xs">
        <input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> {t("board.loop")}
      </label>
      <label className="flex items-center gap-2 text-xs">
        {t("eval.audio")}
        <select className="input !w-auto !max-w-[220px] !py-1" value={audioId} onChange={(e) => setAudioId(e.target.value)} aria-label={t("eval.audio")}>
          <option value="">{t("eval.muted")}</option>
          {audioOptions.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
        </select>
      </label>
    </div>
  );
}

