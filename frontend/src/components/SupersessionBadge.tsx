interface Props {
  count: number;
}

/* Stated calmly, as a feature, not a warning — no red, no exclamation icon. */
export default function SupersessionBadge({ count }: Props) {
  if (count === 0) return null;

  return (
    <p
      className="mt-3 text-[13px] leading-relaxed"
      style={{ color: "var(--superseded-rust)", fontFamily: "var(--font-display)" }}
    >
      {count} superseded version{count !== 1 ? "s" : ""} of this document{" "}
      {count !== 1 ? "were" : "was"} excluded from retrieval.
    </p>
  );
}
