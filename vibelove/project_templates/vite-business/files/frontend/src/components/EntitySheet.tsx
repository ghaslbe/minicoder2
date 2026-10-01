import { useEffect, useRef, useState } from "react";
import { Save, X } from "lucide-react";
import type { CustomerInput } from "../data";

export function EntitySheet({
  initial,
  onSave,
  onClose,
}: {
  initial: CustomerInput | null;
  onSave: (value: CustomerInput) => void;
  onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [value, setValue] = useState<CustomerInput>(
    initial ?? { name: "", email: "", company: "", status: "Interessent" },
  );
  const [error, setError] = useState("");
  useEffect(() => {
    const d = dialog.current!;
    d.showModal();
    return () => d.close();
  }, []);
  return (
    <dialog className="entity-sheet" ref={dialog} onCancel={onClose} aria-labelledby="sheet-title">
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const clean = {
            ...value,
            name: value.name.trim(),
            email: value.email.trim(),
            company: value.company.trim(),
          };
          if (!clean.name || !clean.company) {
            setError("Name und Unternehmen duerfen nicht leer sein.");
            return;
          }
          try {
            onSave(clean);
          } catch {
            setError("Speichern fehlgeschlagen. Bitte den Browserspeicher pruefen.");
          }
        }}
      >
        <header>
          <h2 id="sheet-title">{initial ? "Kunde bearbeiten" : "Neuer Kunde"}</h2>
          <button
            className="icon"
            type="button"
            onClick={onClose}
            title="Schliessen"
            aria-label="Schliessen"
          >
            <X size={18} />
          </button>
        </header>
        <div className="fields">
          <label>
            Name
            <input
              autoFocus
              required
              maxLength={100}
              value={value.name}
              onChange={(e) => setValue({ ...value, name: e.target.value })}
            />
          </label>
          <label>
            Unternehmen
            <input
              required
              maxLength={100}
              value={value.company}
              onChange={(e) => setValue({ ...value, company: e.target.value })}
            />
          </label>
          <label>
            E-Mail
            <input
              required
              type="email"
              maxLength={200}
              value={value.email}
              onChange={(e) => setValue({ ...value, email: e.target.value })}
            />
          </label>
          <label>
            Status
            <select
              value={value.status}
              onChange={(e) =>
                setValue({ ...value, status: e.target.value as CustomerInput["status"] })
              }
            >
              <option>Interessent</option>
              <option>Aktiv</option>
            </select>
          </label>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
        <footer>
          <button type="button" onClick={onClose}>
            Abbrechen
          </button>
          <button className="primary" type="submit">
            <Save size={16} />
            Speichern
          </button>
        </footer>
      </form>
    </dialog>
  );
}
