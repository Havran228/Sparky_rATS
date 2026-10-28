"use client";

import { useEffect, useState, type FormEvent } from "react";

import {
  getProfile,
  listBundles,
  pollJob,
  proposeSkills,
  saveProfile,
  type Bundle,
  type Profile,
  type Skill,
} from "@/lib/knowledge";

const EMPTY: Profile = {
  identity: { name: null },
  contact: { email: null, phone: null, location: null },
  links: [],
  languages: [],
  skills: [],
  preferences: { claim_nczd: true, preferred_contract_types: [], locations: [] },
  updated_at: null,
};

export function ProfileEditor() {
  const [profile, setProfile] = useState<Profile>(EMPTY);
  const [bundles, setBundles] = useState<Bundle[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [links, setLinks] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    Promise.all([getProfile(), listBundles()])
      .then(([loaded, found]) => {
        setProfile(loaded);
        setLinks(loaded.links.join("\n"));
        setBundles(found);
        setSelected(found.map((bundle) => bundle.id));
      })
      .catch((caught: unknown) => {
        setError(caught instanceof Error ? caught.message : "Could not load the profile");
      });
  }, []);

  function updateSkill(index: number, patch: Partial<Skill>) {
    setProfile((current) => ({
      ...current,
      skills: current.skills.map((skill, position) =>
        position === index ? { ...skill, ...patch } : skill,
      ),
    }));
  }

  async function onSave(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const saved = await saveProfile({
        ...profile,
        links: links
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean),
      });
      setProfile(saved);
      setStatus("Saved. Only confirmed skills are used when you run a match.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not save");
    } finally {
      setPending(false);
    }
  }

  async function onPropose() {
    if (selected.length === 0) {
      setError("Select at least one bundle to propose from.");
      return;
    }
    setPending(true);
    setError(null);
    setStatus("Queued");
    try {
      const jobId = await proposeSkills(selected);
      const job = await pollJob(jobId, setStatus);
      if (job.status === "FAILED") {
        setError(job.error ?? "Proposal failed");
      } else {
        const loaded = await getProfile();
        setProfile(loaded);
        setLinks(loaded.links.join("\n"));
        setStatus("Proposed. Confirm the skills you actually have, then save.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Proposal failed");
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={onSave} className="max-w-3xl">
      <h1 className="font-serif text-3xl">Profile</h1>
      <p className="mt-2 text-sm leading-6 text-muted">
        The fast model can propose a skills inventory from your bundles. Nothing is treated as
        evidence until you confirm it. Generation in a later slice will use this same record.
      </p>

      <div className="mt-6 grid gap-3 sm:grid-cols-2">
        <Field
          label="Name"
          value={profile.identity.name ?? ""}
          onChange={(value) =>
            setProfile({ ...profile, identity: { name: value || null } })
          }
        />
        <Field
          label="Email"
          value={profile.contact.email ?? ""}
          onChange={(value) =>
            setProfile({ ...profile, contact: { ...profile.contact, email: value || null } })
          }
        />
        <Field
          label="Phone"
          value={profile.contact.phone ?? ""}
          onChange={(value) =>
            setProfile({ ...profile, contact: { ...profile.contact, phone: value || null } })
          }
        />
        <Field
          label="Location"
          value={profile.contact.location ?? ""}
          onChange={(value) =>
            setProfile({ ...profile, contact: { ...profile.contact, location: value || null } })
          }
        />
      </div>
      <label className="mt-3 block text-sm">
        <span className="text-muted">Links, one per line</span>
        <textarea
          value={links}
          onChange={(event) => setLinks(event.target.value)}
          rows={3}
          className="mt-1 w-full border border-line bg-white/70 px-3 py-2"
        />
      </label>
      <label className="mt-3 flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={profile.preferences.claim_nczd}
          onChange={(event) =>
            setProfile({
              ...profile,
              preferences: { ...profile.preferences, claim_nczd: event.target.checked },
            })
          }
        />
        Claim the non-taxable allowance (NČZD) in the salary panel
      </label>

      <h2 className="mt-8 font-serif text-2xl">Skills</h2>
      <ul className="mt-3 divide-y divide-line border border-line bg-white/50">
        {profile.skills.length === 0 ? (
          <li className="px-4 py-4 text-sm text-muted">No skills yet.</li>
        ) : (
          profile.skills.map((skill, index) => (
            <li key={`${skill.skill}-${index}`} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center">
              <label className="flex items-center gap-2 text-sm sm:w-36">
                <input
                  type="checkbox"
                  checked={skill.confirmed}
                  onChange={(event) => updateSkill(index, { confirmed: event.target.checked })}
                />
                Confirmed
              </label>
              <span className="flex-1 text-sm">{skill.skill}</span>
              <input
                value={skill.years ?? ""}
                onChange={(event) =>
                  updateSkill(index, {
                    years: event.target.value === "" ? null : Number(event.target.value),
                  })
                }
                inputMode="decimal"
                placeholder="Years"
                className="w-24 border border-line bg-white px-2 py-1 text-sm"
              />
              <button
                type="button"
                className="text-sm text-bad"
                onClick={() =>
                  setProfile({
                    ...profile,
                    skills: profile.skills.filter((_, position) => position !== index),
                  })
                }
              >
                Remove
              </button>
            </li>
          ))
        )}
      </ul>

      <fieldset className="mt-6">
        <legend className="text-sm text-muted">Propose from these bundles</legend>
        <div className="mt-2 flex flex-col gap-1">
          {bundles.length === 0 ? <p className="text-sm text-muted">Upload a bundle first.</p> : null}
          {bundles.map((bundle) => (
            <label key={bundle.id} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={selected.includes(bundle.id)}
                onChange={(event) =>
                  setSelected((current) =>
                    event.target.checked
                      ? [...current, bundle.id]
                      : current.filter((id) => id !== bundle.id),
                  )
                }
              />
              {bundle.name}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="mt-4 flex flex-wrap gap-2">
        <button type="submit" disabled={pending} className="bg-spark px-4 py-2 text-sm text-white">
          Save profile
        </button>
        <button
          type="button"
          disabled={pending}
          onClick={onPropose}
          className="border border-ink px-4 py-2 text-sm"
        >
          Propose skills
        </button>
      </div>
      {status ? <p className="mt-3 text-sm text-muted">{status}</p> : null}
      {error ? <p className="mt-3 text-sm text-bad">{error}</p> : null}
    </form>
  );
}

function Field({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted">{label}</span>
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full border border-line bg-white/70 px-3 py-2"
      />
    </label>
  );
}
