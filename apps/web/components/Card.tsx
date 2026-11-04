import { HTMLAttributes } from "react";
import { cn } from "@/lib/cn";

export function Card({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "rounded-xl border border-border bg-bg-card shadow-card",
        className
      )}
      {...rest}
    />
  );
}

export function CardHeader({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        // Brand consistency: card headers use dark navy, like the page header above
        // (#0c2847). Title text is white, secondary (muted) text is light slate; cyan
        // icons/accents stay readable on navy (same visual language as the ribbon).
        "px-5 py-3.5 flex items-center gap-2 rounded-t-xl bg-[#0c2847] text-white",
        "[&_h2]:text-white [&_h3]:text-white [&_.text-muted]:text-slate-400",
        className,
      )}
      {...rest}
    />
  );
}

export function CardBody({ className, ...rest }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-5", className)} {...rest} />;
}
