import { useState } from "react";
import { useI18n } from "../i18n";

/** The visitor's Tokenbay key on the hosted demo: masked by default, optionally remembered on this device. Also the admin secret field. */
export function KeyField({
  value,
  onChange,
  remember,
  onRemember,
  onForget,
  label,
  placeholder = "sk-…",
  note,
  trim = true,
}: {
  value: string;
  onChange: (v: string) => void;
  /** omit onRemember to drop the remember row */
  remember?: boolean;
  onRemember?: (v: boolean) => void;
  onForget?: () => void;
  label?: string;
  placeholder?: string;
  note?: string;
  /** strip surrounding whitespace as typed (Tokenbay keys); false keeps the value exactly as typed (admin passphrase) */
  trim?: boolean;
}) {
  const { t } = useI18n();
  const [show, setShow] = useState(false);
  return (
    <div className="space-y-1.5">
      <label className="block space-y-1">
        <span className="label">{label ?? t("key.label")}</span>
        <span className="flex gap-2">
          <input
            className="input font-mono"
            type={show ? "text" : "password"}
            value={value}
            onChange={(e) => onChange(trim ? e.target.value.trim() : e.target.value)}
            placeholder={placeholder}
            autoComplete="off"
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
          />
          <button type="button" className="btn btn-ghost" onClick={() => setShow(!show)}>
            {show ? t("key.hide") : t("key.show")}
          </button>
        </span>
      </label>
      {onRemember && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-2">
          <label className="flex items-center gap-1.5">
            <input type="checkbox" checked={Boolean(remember)} onChange={(e) => onRemember(e.target.checked)} />
            {t("key.remember")}
          </label>
          {onForget && value && (
            <button type="button" className="text-muted underline hover:text-ink" onClick={onForget}>
              {t("key.forget")}
            </button>
          )}
        </div>
      )}
      <p className="text-xs text-muted">{note ?? t("key.note")}</p>
    </div>
  );
}
