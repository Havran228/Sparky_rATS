"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  calculateNet,
  formatEur,
  salaryYears,
  type SalaryBreakdown,
} from "@/lib/projects";

const CONTRACTS = [
  { value: "TPP", label: "TPP" },
  { value: "SZCO", label: "SZČO" },
  { value: "DOHODA", label: "Dohoda" },
];

export function SalaryPanel({
  initialGross = "",
  initialContract = "TPP",
  note,
}: {
  initialGross?: string;
  initialContract?: string;
  note?: string | null;
}) {
  const [years, setYears] = useState<number[]>([2026, 2025]);
  const [year, setYear] = useState(2026);
  const [gross, setGross] = useState(initialGross);
  const [claim, setClaim] = useState(true);
  const [contract, setContract] = useState(initialContract);
  const [result, setResult] = useState<SalaryBreakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    salaryYears()
      .then((found) => {
        if (found.length > 0) {
          setYears(found);
          setYear(found[found.length - 1] ?? found[0]);
        }
      })
      .catch(() => {
        setYears([2025, 2026]);
      });
  }, []);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      setResult(
        await calculateNet({
          gross,
          taxYear: year,
          claimNczd: claim,
          contractType: contract,
        }),
      );
    } catch (caught) {
      setResult(null);
      setError(caught instanceof Error ? caught.message : "Calculation failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="border border-line bg-white/60 p-4">
      <h2 className="font-serif text-xl">Gross to net</h2>
      <p className="mt-1 text-sm text-muted">
        Employment contract only. The allowance is the Slovak taxpayer allowance (NČZD), reduced
        when the annualised tax base is above the statutory threshold.
      </p>
      {note ? <p className="mt-2 text-sm text-ink">{note}</p> : null}
      <form onSubmit={onSubmit} className="mt-4 grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          Gross monthly EUR
          <input
            value={gross}
            onChange={(event) => setGross(event.target.value)}
            inputMode="decimal"
            className="mt-1 w-full border border-line bg-paper px-2 py-1"
            required
          />
        </label>
        <label className="text-sm">
          Tax year
          <select
            value={year}
            onChange={(event) => setYear(Number(event.target.value))}
            className="mt-1 w-full border border-line bg-paper px-2 py-1"
          >
            {years.map((item) => (
              <option key={item} value={item}>
                {item}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm">
          Contract
          <select
            value={contract}
            onChange={(event) => setContract(event.target.value)}
            className="mt-1 w-full border border-line bg-paper px-2 py-1"
          >
            {CONTRACTS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-end gap-2 text-sm">
          <input
            type="checkbox"
            checked={claim}
            onChange={(event) => setClaim(event.target.checked)}
          />
          Claim NČZD
        </label>
        <button
          type="submit"
          disabled={pending}
          className="bg-ink px-3 py-2 text-sm text-paper disabled:opacity-60 sm:col-span-2"
        >
          {pending ? "Calculating…" : "Calculate take-home"}
        </button>
      </form>
      {error ? <p className="mt-3 text-sm text-bad">{error}</p> : null}
      {result ? <Breakdown result={result} /> : null}
    </section>
  );
}

function Breakdown({ result }: { result: SalaryBreakdown }) {
  if (!result.modeled) {
    return (
      <p className="mt-4 text-sm">
        Published figure {formatEur(result.gross)} is left unconverted. {result.message}
      </p>
    );
  }
  const rows = [
    ["Employee social", result.employee_social_total],
    ["Employee health", result.employee_health],
    ["Tax base", result.tax_base],
    ["NČZD applied", result.nczd_applied],
    ["Tax", result.tax],
    ["Net", result.net],
    ["Employer levies", result.employer_total],
    ["Total cost of work", result.total_cost],
  ] as const;
  return (
    <dl className="mt-4 divide-y divide-line border border-line">
      {rows.map(([label, value]) => (
        <div key={label} className="flex items-center justify-between px-3 py-2 text-sm">
          <dt>{label}</dt>
          <dd className={label === "Net" ? "font-medium text-ink" : "text-muted"}>
            {formatEur(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}
