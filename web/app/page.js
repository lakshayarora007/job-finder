import fs from "fs";
import path from "path";
import JobsTable from "./JobsTable";

export const dynamic = "force-dynamic";

function loadData() {
  const jsonPath = path.join(process.cwd(), "..", "output", "jobs.json");
  try {
    const raw = fs.readFileSync(jsonPath, "utf-8");
    return JSON.parse(raw);
  } catch {
    return { generated_at: null, jobs: [], manual_search: [], days_back: 30 };
  }
}

export default function Home() {
  const data = loadData();

  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100 px-6 py-10 sm:px-12">
      <div className="mx-auto max-w-5xl">
        <h1 className="text-2xl font-semibold tracking-tight">
          Product Manager / Product Analyst / Analyst (Fresher, BTech-friendly) Jobs
        </h1>
        <p className="mt-1 text-sm text-zinc-400">
          Delhi/NCR, Noida, Gurgaon, Hyderabad, Pune, Bangalore, Mumbai · last{" "}
          {data.days_back || 30} days
          {data.generated_at && (
            <> · updated {new Date(data.generated_at).toLocaleString()}</>
          )}
        </p>

        <JobsTable jobs={data.jobs || []} />

        <section className="mt-14">
          <h2 className="text-lg font-semibold">
            Quick search by city (all companies, last 30 days)
          </h2>
          <p className="mt-1 text-sm text-zinc-400">
            Pre-filtered LinkedIn / Naukri / Google / DuckDuckGo searches for
            &quot;Product Manager / APM / Product Analyst / Business Analyst / Product Owner&quot;
            (fresher, BTech-friendly), posted in the last 30 days.
          </p>
          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(data.city_search || []).map((c) => (
              <div
                key={c.city}
                className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3"
              >
                <span className="text-sm font-medium">{c.city}</span>
                <div className="flex flex-wrap justify-end gap-x-3 gap-y-1 text-sm">
                  <a
                    className="text-sky-400 hover:underline"
                    href={c.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    LinkedIn
                  </a>
                  <a
                    className="text-emerald-400 hover:underline"
                    href={c.naukri_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Naukri
                  </a>
                  <a
                    className="text-amber-400 hover:underline"
                    href={c.google_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Google
                  </a>
                  <a
                    className="text-orange-400 hover:underline"
                    href={c.duckduckgo_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    DuckDuckGo
                  </a>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="mt-14">
          <h2 className="text-lg font-semibold">
            Companies worth watching manually
          </h2>
          <p className="mt-1 text-sm text-zinc-400">
            These often have stronger brand / better balance profiles or
            limited public ATS coverage, so the dashboard gives direct search
            links instead of trying fragile scraping.
          </p>
          <div className="mt-4 grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
            {(data.manual_search || []).map((c, i) => (
              <div
                key={`${c.company}-${i}`}
                className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3"
              >
                <span className="text-sm font-medium">{c.company}</span>
                <div className="flex flex-wrap justify-end gap-x-3 gap-y-1 text-sm">
                  <a
                    className="text-sky-400 hover:underline"
                    href={c.linkedin_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    LinkedIn
                  </a>
                  <a
                    className="text-emerald-400 hover:underline"
                    href={c.naukri_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Naukri
                  </a>
                  <a
                    className="text-amber-400 hover:underline"
                    href={c.google_url}
                    target="_blank"
                    rel="noopener noreferrer"
                  >
                    Google
                  </a>
                  {c.career_url && (
                    <a
                      className="text-fuchsia-400 hover:underline"
                      href={c.career_url}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      Careers
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
