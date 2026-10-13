"use client";

import { X } from "lucide-react";
import { ReactNode, useEffect, useId, useRef } from "react";

// Source windows and the category editor share the native top layer. A nested
// editor locks the page once more, but closing it must not unlock its parent.
let scrollLocks = 0;
let previousOverflow = "";

export function SourcePopup({ open, onClose, title, children, footer, className = "", id }: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
  id: string;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef(onClose);
  const headingId = useId();
  closeRef.current = onClose;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!open || !dialog) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (scrollLocks++ === 0) {
      previousOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    dialog.showModal();
    return () => {
      dialog.close();
      if (--scrollLocks === 0) document.body.style.overflow = previousOverflow;
      if (opener?.isConnected) opener.focus({ preventScroll: true });
    };
  }, [open]);

  return (
    <dialog ref={dialogRef} id={id} className={`source-popup ${className}`}
      aria-labelledby={headingId}
      onCancel={(event) => { event.preventDefault(); closeRef.current(); }}
      onClick={(event) => {
        if (event.target !== event.currentTarget) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) {
          closeRef.current();
        }
      }}>
      <header className="source-popup-header">
        <h2 id={headingId}>{title}</h2>
        <button className="source-popup-close" type="button" aria-label={`بستن ${title}`}
          autoFocus onClick={() => closeRef.current()}><X size={20} aria-hidden="true" /></button>
      </header>
      <div className="source-popup-body">{children}</div>
      {footer ? <footer className="source-popup-footer">{footer}</footer> : null}
    </dialog>
  );
}
