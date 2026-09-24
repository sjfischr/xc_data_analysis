"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, type SessionInfo } from "./api";

type SessionState =
  | { status: "loading" }
  | { status: "authenticated"; session: SessionInfo }
  | { status: "unauthenticated" };

/** Client-side route guard (design.md section 12.1's role-aware navigation).
 * Static export has no server to check a cookie before rendering, so an
 * unauthenticated visitor briefly sees this page's shell before the
 * redirect fires -- never its data, since every data fetch also requires
 * the session and fails closed on 401. */
export function useSession(): SessionState {
  const [state, setState] = useState<SessionState>({ status: "loading" });
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    api
      .session()
      .then((session) => {
        if (!cancelled) setState({ status: "authenticated", session });
      })
      .catch((error) => {
        if (cancelled) return;
        if (error instanceof ApiError && error.status === 401) {
          setState({ status: "unauthenticated" });
          router.replace("/login");
        } else {
          setState({ status: "unauthenticated" });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [router]);

  return state;
}
