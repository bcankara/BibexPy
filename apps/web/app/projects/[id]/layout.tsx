/**
 * Static export için placeholder `_` ID — tek bir HTML üretilir, FastAPI
 * catch-all gelen `/projects/<gerçek-id>/...` isteğini bu HTML'e serve eder.
 * Gerçek ID runtime'da `useProjectId()` (window.location) ile okunur.
 *
 * ÖNEMLİ: layout server component olduğundan `params.id` static export'ta
 * `"_"` placeholder'ıdır — bu yüzden ID'yi layout'tan ProjectNav'a GEÇMİYORUZ;
 * ProjectNav (client) kendisi `useProjectId()` ile URL'den okur.
 */
export function generateStaticParams(): { id: string }[] {
  return [{ id: "_" }];
}

export default function ProjectLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // ProjectNav artık PageHeader'ın altında render edilir (koyu header → pipeline → içerik).
  return <>{children}</>;
}
