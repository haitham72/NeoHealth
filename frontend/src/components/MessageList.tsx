import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

export default function MessageList({ children }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  });

  return (
    <div className="flex-1 overflow-y-auto px-6 py-4 flex flex-col gap-3">
      {children}
      <div ref={bottomRef} />
    </div>
  );
}
