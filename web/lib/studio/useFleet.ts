'use client';

/**
 * Shared fleet poll — ONE 15s GET /studio/fleet loop no matter how many
 * components subscribe (the What's-Happening strip in the shell + the FleetBoard
 * on the Runs screen). A module-level store with refcounted subscribers replaces
 * the per-component intervals so mounting the strip adds NO new poll loop.
 *
 * HONESTY: a transport failure flips `error` and keeps the last rows; nothing is
 * fabricated. Rows are the supervisor's real patrol data, verbatim.
 */
import { useEffect, useState } from 'react';

/** One row of GET /studio/fleet — the supervisor's status board. */
export type FleetRow = {
  run_id: string;
  status: string;
  activity: 'working' | 'starting' | 'stalled' | 'waiting-operator' | 'done' | 'failed';
  last_role: string | null;
  last_step_age_s: number | null;
  n_steps: number;
  n_pending_drafts: number;
  n_pending_directives: number;
  n_applied_directives: number;
};

export interface FleetState {
  rows: FleetRow[];
  error: boolean;
  /** ms epoch of the last successful fetch — null before the first one lands. */
  fetchedAt: number | null;
}

const POLL_MS = 15000;

let state: FleetState = { rows: [], error: false, fetchedAt: null };
const subscribers = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;
let inflight = false;

function notify() {
  for (const cb of subscribers) cb();
}

async function load(): Promise<void> {
  if (inflight) return;
  inflight = true;
  try {
    const res = await fetch('/studio/fleet');
    if (!res.ok) throw new Error(String(res.status));
    const d = (await res.json()) as { fleet?: FleetRow[] };
    state = { rows: Array.isArray(d.fleet) ? d.fleet : [], error: false, fetchedAt: Date.now() };
  } catch {
    state = { ...state, error: true };
  } finally {
    inflight = false;
    notify();
  }
}

function ensurePolling() {
  if (timer !== null) return;
  void load();
  timer = setInterval(() => void load(), POLL_MS);
}

function releasePolling() {
  if (subscribers.size === 0 && timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

/** Subscribe to the shared fleet state (starts/stops the single poll loop). */
export function useFleet(): FleetState {
  const [snapshot, setSnapshot] = useState<FleetState>(state);
  useEffect(() => {
    const cb = () => setSnapshot(state);
    subscribers.add(cb);
    ensurePolling();
    cb(); // sync to the current snapshot immediately
    return () => {
      subscribers.delete(cb);
      releasePolling();
    };
  }, []);
  return snapshot;
}

/** Rows that represent live, in-flight work (what the strip/board count). */
export function activeFleetRows(rows: FleetRow[]): FleetRow[] {
  return rows.filter((r) => r.activity !== 'done' && r.activity !== 'failed');
}
