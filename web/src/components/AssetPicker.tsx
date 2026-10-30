import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, IS_PUBLIC } from "../api";
import { useI18n } from "../i18n";
import type { Asset } from "../types";
import { cls } from "../util";
import { Modal } from "./common";

function Thumb({ asset, size = 56 }: { asset: Asset | undefined; size?: number }) {
  if (!asset) return <span className="grid place-items-center rounded-md border border-dashed border-line-strong text-[10px] text-muted" style={{ width: size, height: size }}>—</span>;
  if (asset.kind === "image") return <img src={asset.url} alt={asset.label} className="rounded-md object-cover" style={{ width: size, height: size }} />;
  if (asset.kind === "video")
    return <video src={asset.url} muted className="rounded-md object-cover" style={{ width: size * 1.6, height: size }} />;
  return (
    <span className="grid place-items-center rounded-md bg-surface-3 text-lg" style={{ width: size, height: size }} aria-hidden>
      ♪
    </span>
  );
}

export function AssetChip({ asset, onRemove }: { asset: Asset | undefined; onRemove?: () => void }) {
  const { t } = useI18n();
  if (!asset) return null;
  return (
    <div className="flex items-center gap-2 rounded-lg border border-line bg-surface-2 p-1.5 pr-2.5">
      <Thumb asset={asset} size={40} />
      <div className="min-w-0 text-xs">
        <div className="max-w-[180px] truncate font-medium">{asset.label || asset.filename}</div>
        <div className="text-muted">
          {asset.width ? `${asset.width}×${asset.height}` : asset.duration ? `${asset.duration.toFixed(1)}s` : asset.mime} ·{" "}
          {asset.public_urls.length ? t("assets.has_url") : t("assets.no_url")}
        </div>
      </div>
      {onRemove && (
        <button className="ml-1 text-muted hover:text-ink" onClick={onRemove} aria-label="remove">
          ✕
        </button>
      )}
    </div>
  );
}

export function useAssets() {
  return useQuery({ queryKey: ["assets"], queryFn: api.assets });
}

export default function AssetPicker({
  kind,
  value,
  multiple,
  onChange,
  label,
}: {
  kind: "image" | "video" | "audio";
  value: string | string[] | undefined;
  multiple?: boolean;
  onChange: (v: string | string[] | undefined) => void;
  label: string;
}) {
  const { t } = useI18n();
  const qc = useQueryClient();
  const { data: assets = [] } = useAssets();
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"library" | "upload" | "url">("library");
  const [url, setUrl] = useState("");
  const [publicUrl, setPublicUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const ids = Array.isArray(value) ? value : value ? [value] : [];
  const byId = new Map(assets.map((a) => [a.id, a]));
  const pool = assets.filter((a) => a.kind === kind);

  const choose = (id: string) => {
    if (multiple) onChange(ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]);
    else {
      onChange(id);
      setOpen(false);
    }
  };

  const add = async (fn: () => Promise<Asset>) => {
    setBusy(true);
    setErr(null);
    try {
      const a = await fn();
      await qc.invalidateQueries({ queryKey: ["assets"] });
      choose(a.id);
      setTab("library");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="label">{label}</div>
      <div className="flex flex-wrap items-center gap-2">
        {ids.map((id) => (
          <AssetChip key={id} asset={byId.get(id)} onRemove={() => onChange(multiple ? ids.filter((x) => x !== id) : undefined)} />
        ))}
        <button className="btn" onClick={() => setOpen(true)}>
          {t("assets.choose")}
        </button>
      </div>
      <Modal open={open} onClose={() => setOpen(false)} title={label} wide>
        <div className="mb-4 seg">
          {(IS_PUBLIC ? (["library", "upload"] as const) : (["library", "upload", "url"] as const)).map((k) => (
            <button key={k} aria-pressed={tab === k} onClick={() => setTab(k)}>
              {t(`assets.${k}`)}
            </button>
          ))}
        </div>
        {tab === "library" && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
            {pool.map((a) => (
              <button
                key={a.id}
                onClick={() => choose(a.id)}
                className={cls("card overflow-hidden text-left transition", ids.includes(a.id) ? "ring-2 ring-[var(--focus)]" : "hover:border-line-strong")}
              >
                {a.kind === "image" ? (
                  <img src={a.url} alt={a.label} className="aspect-square w-full object-cover" />
                ) : a.kind === "video" ? (
                  <video src={a.url} muted className="aspect-square w-full object-cover" />
                ) : (
                  <div className="grid aspect-square w-full place-items-center bg-surface-3 text-3xl">♪</div>
                )}
                <div className="p-2 text-xs">
                  <div className="truncate font-medium">{a.label || a.filename}</div>
                  <div className="text-muted">{a.public_urls.length ? t("assets.has_url") : t("assets.no_url")}</div>
                </div>
              </button>
            ))}
            {pool.length === 0 && <div className="text-sm text-muted">—</div>}
          </div>
        )}
        {tab === "upload" && (
          <div className="space-y-3">
            <input ref={fileRef} type="file" accept={kind === "image" ? "image/*" : kind === "video" ? "video/*" : "audio/*"} className="input" />
            <div>
              <div className="label mb-1">{t("assets.public_url")}</div>
              <input className="input" value={publicUrl} onChange={(e) => setPublicUrl(e.target.value)} placeholder="https://…" />
            </div>
            <button
              className="btn btn-primary"
              disabled={busy}
              onClick={() => {
                const f = fileRef.current?.files?.[0];
                if (f) add(() => api.uploadAsset(f, f.name, publicUrl));
              }}
            >
              {t("assets.add")}
            </button>
          </div>
        )}
        {tab === "url" && (
          <div className="space-y-3">
            <input className="input" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…/image.png" />
            <button className="btn btn-primary" disabled={busy || !url} onClick={() => add(() => api.assetFromUrl(url, ""))}>
              {t("assets.add")}
            </button>
          </div>
        )}
        {err && <p className="mt-3 text-sm" style={{ color: "var(--critical)" }}>{err}</p>}
      </Modal>
    </div>
  );
}
