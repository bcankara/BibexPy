/**
 * Placeholder `_` ID for the static export — a single HTML file is produced, and the
 * FastAPI catch-all serves incoming `/projects/<real-id>/...` requests with it.
 * The real ID is read at runtime via `useProjectId()` (window.location).
 *
 * IMPORTANT: since the layout is a server component, `params.id` is the `"_"`
 * placeholder in the static export — so we do NOT pass the ID from the layout to
 * ProjectNav; ProjectNav (client) reads it from the URL itself via `useProjectId()`.
 */
export function generateStaticParams(): { id: string }[] {
  return [{ id: "_" }];
}

export default function ProjectLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // ProjectNav is now rendered below PageHeader (dark header → pipeline → content).
  return <>{children}</>;
}
