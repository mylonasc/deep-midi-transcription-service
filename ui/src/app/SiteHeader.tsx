/** Site header with connection indicator. No job/data logic. */
export default function SiteHeader({ loaded, connectionError }: {
  loaded: boolean;
  connectionError: string | null;
}) {
  return <header className="site-header"><div className="header-inner">
    <div className="brand-mark" aria-hidden="true">♫</div>
    <div><p className="eyebrow">DEEP MIDI</p><h1>Your music, transcribed.</h1><p className="header-copy">From YouTube to MIDI, in one queue.</p></div>
    <span className={`connection ${connectionError ? "offline" : ""}`}><span aria-hidden="true">●</span> {connectionError ? "Reconnecting" : loaded ? "Connected" : "Connecting"}</span>
  </div></header>;
}
