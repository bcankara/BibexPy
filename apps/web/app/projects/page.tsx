"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, formatBytes, type Project, translateApiError} from "@/lib/api-client";
import { Button } from "@/components/Button";
import { Card, CardBody } from "@/components/Card";
import { PageHeader } from "@/components/PageHeader";
import { FolderOpen, Plus, Trash2, FileText, ArrowRight, Loader2 } from "lucide-react";
import { useT } from "@/lib/i18n";
import { useConfirm } from "@/components/Dialogs";

export default function ProjectsPage() {
  const t = useT();
  const confirm = useConfirm();
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    try { setProjects(await api.listProjects()); }
    catch (e) { setError(translateApiError(t, e)); }
  }
  useEffect(() => { load(); }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true); setError(null);
    try {
      const p = await api.createProject(newName.trim());
      setNewName("");
      await load();
      window.location.href = `/projects/${p.id}/merge`;
    } catch (e) {
      setError(translateApiError(t, e));
      setBusy(false);
    }
  }

  async function handleDelete(id: string, name: string) {
    const ok = await confirm({
      title: t("projects.deleteConfirm"),
      message: `"${name}"`,
      detail: t("projects.deleteWarning"),
      tone: "danger",
    });
    if (!ok) return;
    try { await api.deleteProject(id); await load(); }
    catch (e) { setError(translateApiError(t, e)); }
  }

  return (
    <>
      <PageHeader
        title={t("projects.title")}
        subtitle={t("projects.subtitle")}
        badges={[{ label: t("nav.projects"), tone: "neutral" }]}
      />

      <div className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        {error && (
          <div className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm text-red-700">
            {error}
          </div>
        )}

        <Card>
          <CardBody>
            <form onSubmit={handleCreate} className="flex gap-2 items-center">
              <div className="w-9 h-9 rounded-lg bg-[#0c2847] text-cyan-200 flex items-center justify-center">
                <Plus className="h-4 w-4" />
              </div>
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder={t("projects.newProjectPlaceholder")}
                className="flex-1 rounded-lg border border-border bg-white px-3 py-2 text-sm focus:outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-500/20"
                maxLength={120}
              />
              <Button type="submit" disabled={busy || !newName.trim()}>
                {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                {t("projects.create")}
              </Button>
            </form>
          </CardBody>
        </Card>

        {projects === null ? (
          <p className="text-muted text-sm">{t("common.loading")}</p>
        ) : projects.length === 0 ? (
          <Card>
            <CardBody className="text-center py-14 text-muted">
              <FolderOpen className="h-12 w-12 mx-auto mb-3 text-border" />
              <p className="font-medium text-ink mb-1">{t("projects.noProjects")}</p>
              <p className="text-sm">{t("projects.noProjectsHint")}</p>
            </CardBody>
          </Card>
        ) : (
          <div className="grid md:grid-cols-2 gap-3">
            {projects.map((p) => (
              <Card key={p.id} className="hover:shadow-soft transition">
                <CardBody className="flex items-start gap-3">
                  <div className="w-10 h-10 rounded-lg bg-[#0c2847] text-cyan-200 flex items-center justify-center flex-shrink-0">
                    <FolderOpen className="h-5 w-5" />
                  </div>
                  <div className="flex-1 min-w-0">
                    <Link
                      href={`/projects/${p.id}/merge`}
                      className="font-semibold text-ink hover:text-brand-600 truncate block"
                    >
                      {p.name}
                    </Link>
                    <p className="text-xs text-muted mt-1 flex items-center gap-3 flex-wrap">
                      <span className="inline-flex items-center gap-1">
                        <FileText className="h-3 w-3" />{t("projects.fileCount", { n: p.file_count })}
                      </span>
                      <span>{formatBytes(p.raw_size_bytes)}</span>
                      <span>{new Date(p.created_at).toLocaleDateString()}</span>
                    </p>
                  </div>
                  <Link href={`/projects/${p.id}/merge`}>
                    <Button variant="ghost" size="sm">
                      <ArrowRight className="h-4 w-4" />
                    </Button>
                  </Link>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDelete(p.id, p.name)}
                    title={t("common.delete")}
                  >
                    <Trash2 className="h-4 w-4 text-muted hover:text-danger" />
                  </Button>
                </CardBody>
              </Card>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
