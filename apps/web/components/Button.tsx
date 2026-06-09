"use client";
import { ButtonHTMLAttributes, forwardRef } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
}

const styles: Record<Variant, string> = {
  primary: "bg-primary text-white hover:bg-primary-hover shadow-sm",
  secondary: "bg-white text-ink border border-border hover:bg-bg-soft",
  ghost: "text-ink hover:bg-bg-soft",
  danger: "bg-danger text-white hover:bg-red-700 shadow-sm",
  success: "bg-success text-white hover:bg-emerald-600 shadow-sm",
};

export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { className, variant = "primary", size = "md", ...rest }, ref
) {
  return (
    <button
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg font-medium transition disabled:opacity-50 disabled:pointer-events-none focus:outline-none focus:ring-2 focus:ring-brand-500/40",
        size === "sm" ? "px-3 py-1.5 text-sm" : "px-4 py-2 text-sm",
        styles[variant],
        className
      )}
      {...rest}
    />
  );
});
