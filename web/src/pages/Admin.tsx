import { useState, type FormEvent } from "react";
import { adminLogin, adminLogout, ApiError, IS_ADMIN } from "../api";
import { useI18n } from "../i18n";
import { ErrorBox, Spinner } from "../components/common";
import { KeyField } from "../components/KeyField";
import { useConfig } from "../components/RunSetup";

/** Hosted site only (the route redirects home on the local instance), and not linked from the nav. */
export default function AdminPage() {
  const { t } = useI18n();
  const { data: cfg } = useConfig();
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const disabled = cfg?.admin_login === false;
  // the page was rendered for an admin, but the server no longer agrees: the session expired
  const signedIn = IS_ADMIN && cfg?.admin !== false;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!secret || busy) return;
    setBusy(true);
    setErr(null);
    try {
      await adminLogin(secret); // exactly as typed: spaces are part of a passphrase
      window.location.replace("/");
    } catch (x) {
      const status = x instanceof ApiError ? x.status : 0;
      setErr(status === 401 ? new Error(t("admin.wrong")) : status === 429 ? new Error(t("admin.throttled")) : status === 404 ? new Error(t("admin.disabled")) : x);
      setBusy(false);
    }
  };

  const signOut = async () => {
    setBusy(true);
    await adminLogout().catch(() => undefined);
    window.location.replace("/");
  };

  return (
    <div className="mx-auto max-w-md space-y-4 pt-6">
      <h1 className="text-2xl font-semibold tracking-tight">{t("admin.title")}</h1>
      {signedIn ? (
        <div className="card space-y-3 p-4">
          <p className="text-[13px] text-ink-2">{t("admin.signed_in")}</p>
          <button className="btn" disabled={busy} onClick={signOut}>
            {t("admin.signout")}
          </button>
        </div>
      ) : (
        <form className="card space-y-3 p-4" onSubmit={submit}>
          {disabled ? (
            <p className="text-[13px] text-ink-2">{t("admin.disabled")}</p>
          ) : (
            <>
              <KeyField value={secret} onChange={setSecret} trim={false} label={t("admin.secret")} placeholder="" note={t("admin.note")} />
              <ErrorBox error={err} />
              <button type="submit" className="btn btn-primary" disabled={!secret || busy}>
                {busy ? <Spinner className="text-surface" /> : null}
                {t("admin.submit")}
              </button>
            </>
          )}
        </form>
      )}
    </div>
  );
}
