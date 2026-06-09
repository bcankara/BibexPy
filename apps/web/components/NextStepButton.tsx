"use client";
import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { useT } from "@/lib/i18n";

/**
 * Pipeline'da bir sonraki adıma geçiş butonu — koyu PageHeader üzerinde
 * BELİRGİN primary cyan stil. Üstte küçük "NEXT STEP / SONRAKİ ADIM" etiketi,
 * altta hedef adımın adı, sağda chip içinde animasyonlu ok. Kullanıcının
 * nereye tıklayacağı bir bakışta net.
 *
 * Kullanım: <NextStepButton href={`/projects/${id}/records`} label={t("nav.records")} />
 */
export function NextStepButton({ href, label }: { href: string; label: string }) {
  const t = useT();
  return (
    <Link
      href={href}
      className="group inline-flex items-center gap-3 rounded-xl bg-teal-600 py-2.5 pl-5 pr-3 text-white shadow-[0_16px_40px_-12px_rgba(15,118,110,0.9)] ring-1 ring-teal-300/40 transition hover:-translate-y-0.5 hover:bg-teal-500"
    >
      <span className="flex flex-col items-start leading-none">
        <span className="text-[10px] font-bold uppercase tracking-[0.18em] text-teal-50/90">
          {t("common.nextStep")}
        </span>
        <span className="mt-1 whitespace-nowrap text-base font-extrabold">{label}</span>
      </span>
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-white/20 transition group-hover:bg-white/30">
        <ArrowRight className="h-5 w-5 transition-transform group-hover:translate-x-0.5" />
      </span>
    </Link>
  );
}
