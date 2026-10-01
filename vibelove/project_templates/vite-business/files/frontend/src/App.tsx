import { useEffect, useState } from "react";
import { Users, Plus, Search, Pencil, Trash2, RotateCw, LayoutDashboard } from "lucide-react";
import { loadCustomers, saveCustomers } from "./data";
import type { Customer, CustomerInput } from "./data";
import { PageHeader } from "./components/PageHeader";
import { DataTable } from "./components/DataTable";
import { EntitySheet } from "./components/EntitySheet";
import { ConfirmDelete } from "./components/ConfirmDelete";

export default function App() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("Alle");
  const [view, setView] = useState("customers");
  const [editing, setEditing] = useState<Customer | null | undefined>(undefined);
  const [deleting, setDeleting] = useState<Customer | null>(null);
  const [notice, setNotice] = useState("");
  function load() {
    setLoading(true);
    try {
      setCustomers(loadCustomers());
      setError("");
    } catch {
      setError("Kundendaten konnten nicht geladen werden.");
    } finally {
      setLoading(false);
    }
  }
  useEffect(load, []);
  function update(next: Customer[], message: string) {
    saveCustomers(next);
    setCustomers(next);
    setNotice(message);
  }
  function save(value: CustomerInput) {
    const next = editing
      ? customers.map((c) => (c.id === editing.id ? { ...c, ...value } : c))
      : [...customers, { ...value, id: crypto.randomUUID() }];
    update(next, "Kunde gespeichert.");
    setEditing(undefined);
  }
  const filtered = customers.filter(
    (c) =>
      (status === "Alle" || c.status === status) &&
      `${c.name} ${c.company} ${c.email}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()),
  );
  return (
    <div className="app">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Kontor Startseite">
          <img src="/brand.svg" alt="" />
          Kontor
        </a>
        <nav aria-label="Hauptnavigation">
          <button
            aria-label="Uebersicht"
            title="Uebersicht"
            aria-current={view === "dashboard" ? "page" : undefined}
            onClick={() => setView("dashboard")}
          >
            <LayoutDashboard size={18} />
            <span>Uebersicht</span>
          </button>
          <button
            aria-label="Kunden"
            title="Kunden"
            aria-current={view === "customers" ? "page" : undefined}
            onClick={() => setView("customers")}
          >
            <Users size={18} />
            <span>Kunden</span>
            <span className="count">{customers.length}</span>
          </button>
        </nav>
        <div className="workspace">
          <span className="avatar">K</span>
          <div>
            Mein Arbeitsbereich<small>Lokal</small>
          </div>
        </div>
      </aside>
      <main>
        <div className="topbar">
          <span>Arbeitsbereich / {view === "customers" ? "Kunden" : "Uebersicht"}</span>
          <span className="local-dot">Lokal gespeichert</span>
        </div>
        <section className="content">
          <PageHeader
            title={view === "customers" ? "Kunden" : "Uebersicht"}
            subtitle="Kontakte und Unternehmen"
            action={
              <button
                className="primary"
                disabled={loading || !!error}
                onClick={() => setEditing(null)}
              >
                <Plus size={17} />
                Neuer Kunde
              </button>
            }
          />
          {error ? (
            <div role="alert" className="error">
              {error}
              <button onClick={load}>
                <RotateCw size={16} />
                Erneut laden
              </button>
            </div>
          ) : (
            <>
              {view === "dashboard" && (
                <dl className="metrics">
                  <div>
                    <dt>Kunden gesamt</dt>
                    <dd>{customers.length}</dd>
                  </div>
                  <div>
                    <dt>Aktiv</dt>
                    <dd>{customers.filter((c) => c.status === "Aktiv").length}</dd>
                  </div>
                  <div>
                    <dt>Interessenten</dt>
                    <dd>{customers.filter((c) => c.status === "Interessent").length}</dd>
                  </div>
                </dl>
              )}
              <div className="filterbar">
                <label className="search">
                  <Search size={17} />
                  <input
                    aria-label="Kunden suchen"
                    placeholder="Name, Unternehmen, E-Mail suchen"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                  />
                </label>
                <label className="status-filter">
                  Status
                  <select value={status} onChange={(e) => setStatus(e.target.value)}>
                    <option>Alle</option>
                    <option>Aktiv</option>
                    <option>Interessent</option>
                  </select>
                </label>
              </div>
              <DataTable
                rows={filtered}
                loading={loading}
                columns={[
                  {
                    key: "name",
                    label: "Kontakt",
                    render: (c) => (
                      <div className="contact">
                        <span className="avatar">{c.name.slice(0, 1)}</span>
                        <div>
                          <strong>{c.name}</strong>
                          <a href={`mailto:${c.email}`}>{c.email}</a>
                        </div>
                      </div>
                    ),
                  },
                  { key: "company", label: "Unternehmen", render: (c) => c.company },
                  {
                    key: "status",
                    label: "Status",
                    render: (c) => (
                      <span className={`badge ${c.status === "Aktiv" ? "active" : "pending"}`}>
                        {c.status}
                      </span>
                    ),
                  },
                  {
                    key: "actions",
                    label: "Aktionen",
                    render: (c) => (
                      <div className="actions">
                        <button
                          className="icon"
                          title="Bearbeiten"
                          aria-label={`${c.name} bearbeiten`}
                          onClick={() => setEditing(c)}
                        >
                          <Pencil size={16} />
                        </button>
                        <button
                          className="icon"
                          title="Loeschen"
                          aria-label={`${c.name} loeschen`}
                          onClick={() => setDeleting(c)}
                        >
                          <Trash2 size={16} />
                        </button>
                      </div>
                    ),
                  },
                ]}
              />
              <div className="table-footer">
                {filtered.length} von {customers.length} Kontakten
                <span role="status">{notice}</span>
              </div>
            </>
          )}
        </section>
      </main>
      {editing !== undefined && (
        <EntitySheet initial={editing} onSave={save} onClose={() => setEditing(undefined)} />
      )}
      {deleting && (
        <ConfirmDelete
          name={deleting.name}
          onClose={() => setDeleting(null)}
          onConfirm={() => {
            update(
              customers.filter((c) => c.id !== deleting.id),
              "Kunde geloescht.",
            );
            setDeleting(null);
          }}
        />
      )}
    </div>
  );
}
