export default function SettingsPage() {
  return (
    <div className="max-w-2xl">
      <h1 className="font-serif text-3xl">Settings</h1>
      <p className="mt-3 text-sm leading-6 text-muted">
        Model roles, the API bearer, and the MCP bearer are environment and credential-store
        settings. The gross-to-net toggle, Google revoke, and the external-tool whitelist arrive
        with later slices. Nothing on this page calls a cloud model.
      </p>
    </div>
  );
}
