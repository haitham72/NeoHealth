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
    <div className="flex-1 overflow-y-auto px-4 py-4 sm:px-6 flex flex-col gap-3">
      {children}
      <div ref={bottomRef} />
    </div>
  );
}
