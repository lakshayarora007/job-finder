"use client";

import { useMemo, useState } from "react";

export default function JobsTable({ jobs }) {
  const [query, setQuery] = useState("");
  const [sourceFilter, setSourceFilter] = useState("all");
  const [sortBy, setSortBy] = useState("relevance");

  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    const next = jobs.filter((j) => {
      const haystack = `${j.title} ${j.company} ${j.location} ${j.source || ""}`.toLowerCase();
      const queryMatch = !q || haystack.includes(q);
      const sourceMatch = sourceFilter === "all" || (j.source || "unknown") === sourceFilter;
      return queryMatch && sourceMatch;
    });

    if (sortBy === "relevance") {
      return [...next].sort((a, b) => {
        const scoreDiff = (b.score || 0) - (a.score || 0);
        if (scoreDiff !== 0) return scoreDiff;
        return String(b.posted_date || "").localeCompare(String(a.posted_date || ""));
      });
    }
    if (sortBy === "company") {
      return [...next].sort((a, b) => a.company.localeCompare(b.company));
    }
    return [...next].sort((a, b) => String(b.posted_date || "").localeCompare(String(a.posted_date || "")));
  }, [jobs, query, sortBy, sourceFilter]);

  const sources = useMemo(() => {
    const values = new Set(["all"]);
    jobs.forEach((j) => values.add(j.source || "unknown"));
    return Array.from(values);
  }, [jobs]);

  return (
    <div className="mt-6">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
        <div className="grid w-full gap-3 sm:grid-cols-2 lg:max-w-2xl">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search title, company, city, source..."
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-zinc-600"
          />
          <select
            value={sourceFilter}
            onChange={(e) => setSourceFilter(e.target.value)}
            className="w-full rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-zinc-600"
          >
            {sources.map((source) => (
              <option key={source} value={source}>
                {source === "all" ? "All sources" : source}
              </option>
            ))}
          </select>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-xs uppercase tracking-wide text-zinc-500">Sort</label>
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value)}
            className="rounded-md border border-zinc-800 bg-zinc-900 px-3 py-2 text-sm outline-none focus:border-zinc-600"
          >
            <option value="relevance">Relevance</option>
            <option value="latest">Latest</option>
            <option value="company">Company</option>
          </select>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs text-zinc-400">
        <span className="rounded-full border border-zinc-800 bg-zinc-900 px-2 py-1">
          {filtered.length} results
        </span>
        <span className="rounded-full border border-zinc-800 bg-zinc-900 px-2 py-1">
          Fresher-first scoring
        </span>
        <span className="rounded-full border border-zinc-800 bg-zinc-900 px-2 py-1">
          Cities + sources indexed
        </span>
      </div>

      {filtered.length === 0 ? (
        <div className="mt-6 rounded-lg border border-dashed border-zinc-800 bg-zinc-950/60 p-4 text-sm text-zinc-500">
          <p>No matching jobs right now from the tracked companies.</p>
          <p className="mt-1">
            Try clearing source filter, changing sort to Latest, or widening the company list in companies.json.
          </p>
        </div>
      ) : (
        <div className="mt-4 overflow-x-auto rounded-lg border border-zinc-800">
          <table className="w-full text-sm">
            <thead className="bg-zinc-900 text-left text-zinc-400">
              <tr>
                <th className="px-4 py-2 font-medium">Title</th>
                <th className="px-4 py-2 font-medium">Company</th>
                <th className="px-4 py-2 font-medium">Location</th>
                <th className="px-4 py-2 font-medium">Posted</th>
                <th className="px-4 py-2 font-medium">Source</th>
                <th className="px-4 py-2 font-medium">Link</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((j, i) => (
                <tr
                  key={`${j.company}-${j.title}-${i}`}
                  className="border-t border-zinc-800 hover:bg-zinc-900/60"
                >
                  <td className="px-4 py-2">{j.title}</td>
                  <td className="px-4 py-2">{j.company}</td>
                  <td className="px-4 py-2">{j.location}</td>
                  <td className="px-4 py-2 text-zinc-400">{j.posted_date}</td>
                  <td className="px-4 py-2 text-zinc-400">{j.source || "unknown"}</td>
                  <td className="px-4 py-2">
                    <a
                      className="text-sky-400 hover:underline"
                      href={j.url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Apply
                    </a>
                    {j.post_url && (
                      <a
                        className="ml-3 text-zinc-400 hover:underline"
                        href={j.post_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Post
                      </a>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
