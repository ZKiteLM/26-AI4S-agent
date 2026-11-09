"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Update<T> = T | ((previous: T) => T);

function scopedKey(name: string): string {
  let owner = "guest";
  try {
    const body = window.localStorage.getItem("research-token")?.split(".")[0];
    if (body) {
      const base64 = body.replaceAll("-", "+").replaceAll("_", "/");
      const payload = JSON.parse(
        atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, "=")),
      );
      owner = String(payload.sub || owner);
    }
  } catch {
    // An invalid token should not prevent editing local drafts.
  }
  return `research-copilot:v2:${encodeURIComponent(owner)}:${name}`;
}

/** Keep an editor's state while navigating routes or tabs in this browser session. */
export function useSessionState<T>(
  name: string,
  initial: T,
): [T, (update: Update<T>) => void] {
  const [value, setValue] = useState<T>(initial);
  const latest = useRef(value);
  const edited = useRef(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => {
      if (edited.current) return;
      try {
        const saved = window.sessionStorage.getItem(scopedKey(name));
        if (saved !== null) {
          const restored = JSON.parse(saved) as T;
          latest.current = restored;
          setValue(restored);
        }
      } catch {
        // Malformed or unavailable session data falls back to the initial value.
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [name]);
  const update = useCallback(
    (next: Update<T>) => {
      edited.current = true;
      const resolved =
        typeof next === "function"
          ? (next as (previous: T) => T)(latest.current)
          : next;
      latest.current = resolved;
      try {
        window.sessionStorage.setItem(
          scopedKey(name),
          JSON.stringify(resolved),
        );
      } catch {
        // The editor remains usable if browser storage is full or disabled.
      }
      setValue(resolved);
    },
    [name],
  );
  return [value, update];
}
