interface Props {
  content: string;
}

export default function UserMessage({ content }: Props) {
  return (
    <div className="flex justify-end">
      <div
        className="max-w-[70%] rounded-2xl rounded-tr-sm px-4 py-2.5 text-[14px]"
        style={{ background: "var(--fhir-blue)", color: "#fff", fontFamily: "var(--font-display)" }}
      >
        {content}
      </div>
    </div>
  );
}
