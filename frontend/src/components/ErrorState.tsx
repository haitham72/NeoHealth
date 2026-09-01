interface Props {
  detail: string;
}

export default function ErrorState({ detail }: Props) {
  return (
    <div
      className="rounded-md p-6 sm:p-8"
      style={{
        background: "var(--superseded-rust-bg)",
        border: "1px solid var(--superseded-rust)",
      }}
    >
      <p
        className="text-[13px] font-semibold"
        style={{ color: "var(--superseded-rust)", fontFamily: "var(--font-display)" }}
      >
        The request didn't complete.
      </p>
      <p
        className="mt-1 text-[13px]"
        style={{ color: "var(--ink-dim)", fontFamily: "var(--font-display)" }}
      >
        {detail}
      </p>
    </div>
  );
}
