import { useEvictCache } from "../api/client";

interface Props {
  /** Signed token minted with a cached response; when absent the note renders without
   * the remove control (older payloads / minting failures). */
  token?: string;
  /** Spacing/typography overrides so each call site keeps its existing layout. */
  className?: string;
}

/** "Served from cache" note shown under cached results, with a light-red control that
 * evicts ONLY this result -- the signed token is bound to the exact cache entry the
 * user was shown, never a whole-cache purge. */
export default function CacheNotice({ token, className = "mt-1 text-[11px]" }: Props) {
  const evict = useEvictCache();

  if (evict.isSuccess) {
    return (
      <div className={className} style={{ color: "var(--ink-faint)" }}>
        Removed from cache
      </div>
    );
  }

  return (
    <div className={`flex items-center gap-2 ${className}`} style={{ color: "var(--ink-faint)" }}>
      <span>Served from cache</span>
      {token && (
        <button
          type="button"
          onClick={() => evict.mutate(token)}
          disabled={evict.isPending}
          title="Remove only this result from the cache"
          className="inline-flex items-center gap-1 transition-opacity hover:opacity-80 disabled:cursor-not-allowed disabled:opacity-50"
          style={{ color: "var(--danger-soft)" }}
        >
          <svg
            width="11"
            height="11"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <polyline points="3 6 5 6 21 6" />
            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
            <path d="M10 11v6M14 11v6" />
            <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
          </svg>
          Remove from cache
        </button>
      )}
    </div>
  );
}
