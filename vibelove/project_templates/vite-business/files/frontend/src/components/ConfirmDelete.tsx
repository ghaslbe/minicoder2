import { useEffect, useRef, useState } from "react";
import { Trash2 } from "lucide-react";

export function ConfirmDelete({
  name,
  onConfirm,
  onClose,
}: {
  name: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const d = dialog.current!;
    d.showModal();
    return () => d.close();
  }, []);
  return (
    <dialog className="confirm" ref={dialog} onCancel={onClose} aria-labelledby="delete-title">
      <h2 id="delete-title">Kunde loeschen?</h2>
      <p>{name} wird dauerhaft entfernt.</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <footer>
        <button autoFocus onClick={onClose}>
          Abbrechen
        </button>
        <button
          className="danger"
          onClick={() => {
            try {
              onConfirm();
            } catch {
              setError("Loeschen fehlgeschlagen. Bitte erneut versuchen.");
            }
          }}
        >
          <Trash2 size={16} />
          Loeschen
        </button>
      </footer>
    </dialog>
  );
}
