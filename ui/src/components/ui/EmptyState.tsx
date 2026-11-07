import type { ReactNode } from "react";

/** Dashed placeholder panel. Keeps the established `.empty-state` styling. */
export default function EmptyState({ title, children }: {
  title?: string;
  children: ReactNode;
}) {
  return <div className="empty-state" role="status">
    {title && <h3>{title}</h3>}
    {typeof children === "string" ? <p>{children}</p> : children}
  </div>;
}
