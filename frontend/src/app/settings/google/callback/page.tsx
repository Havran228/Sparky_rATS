"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";

import { exchangeGoogleCode } from "@/lib/slice4";

export default function GoogleCallbackPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted">Connecting Google…</p>}>
      <Exchange />
    </Suspense>
  );
}

function Exchange() {
  const params = useSearchParams();
  const [message, setMessage] = useState("Connecting Google…");

  useEffect(() => {
    const error = params.get("error");
    const code = params.get("code");
    const state = params.get("state");
    if (error) {
      setMessage(error);
      return;
    }
    if (!code || !state) {
      setMessage("Google did not return a code.");
      return;
    }
    exchangeGoogleCode(code, state)
      .then(() => setMessage("Google is connected. Tokens stay on this machine."))
      .catch((caught: unknown) => {
        setMessage(caught instanceof Error ? caught.message : "Could not exchange the code");
      });
  }, [params]);

  return (
    <div className="max-w-xl">
      <h1 className="font-serif text-3xl">Google</h1>
      <p className="mt-3 text-sm">{message}</p>
      <Link href="/settings" className="mt-4 inline-block text-sm text-spark">
        Back to settings
      </Link>
    </div>
  );
}
