import type { ReactNode } from "react";

/** Inline error/status banner. Keeps the established `.notice` styling. */
export default function Notice({ children, onDismiss, status = false }: {
  children: ReactNode;
  onDismiss?: () => void;
  status?: boolean;
}) {
  return <div className="notice" role={status ? "status" : "alert"}>
    <span>{children}</span>
    {onDismiss && <button onClick={onDismiss}>Dismiss</button>}
  </div>;
}
