export type Customer = {
  id: string;
  name: string;
  email: string;
  company: string;
  status: "Aktiv" | "Interessent";
};
export type CustomerInput = Omit<Customer, "id">;
import project from "./project.json";
const key = project.storageKey;
const seed: Customer[] = [
  {
    id: "1",
    name: "Mara Winter",
    email: "mara@example.com",
    company: "Studio Winter",
    status: "Aktiv",
  },
  {
    id: "2",
    name: "Jonas Berg",
    email: "jonas@example.com",
    company: "Berg & Partner",
    status: "Aktiv",
  },
  {
    id: "3",
    name: "Lea Sommer",
    email: "lea@example.com",
    company: "Sommer Design",
    status: "Interessent",
  },
  {
    id: "4",
    name: "Emil Roth",
    email: "emil@example.com",
    company: "Roth Architektur",
    status: "Aktiv",
  },
];

export function loadCustomers(): Customer[] {
  const raw = localStorage.getItem(key);
  if (raw === null) return seed;
  const data: unknown = JSON.parse(raw);
  if (
    !Array.isArray(data) ||
    !data.every(
      (c) =>
        c &&
        typeof c.id === "string" &&
        typeof c.name === "string" &&
        typeof c.company === "string" &&
        typeof c.email === "string" &&
        ["Aktiv", "Interessent"].includes(c.status),
    ) ||
    new Set(data.map((c) => c.id)).size !== data.length
  ) {
    throw new Error("Gespeicherte Kundendaten sind ungueltig.");
  }
  return data;
}

export function saveCustomers(customers: Customer[]) {
  localStorage.setItem(key, JSON.stringify(customers));
}
