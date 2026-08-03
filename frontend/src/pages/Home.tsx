/**
 * Home page.
 *
 * Scaffold version: the title only. The search bar (submitting to `/cases?q=…`), the Europe
 * map and the latest-cases sidebar are added by the issues that own them
 * (`docs/architecture.md` section 6).
 */

export default function Home(): JSX.Element {
  return (
    <section className="space-y-4">
      <h1 className="text-wur-green-darker text-3xl font-bold sm:text-4xl">
        Pesticide Litigation Tracker (PLT)
      </h1>
      <p className="text-wur-muted max-w-2xl">
        An open-access, automatically updated database of pesticide-related case law from the
        European Union and its member states, by the Law group of Wageningen University &amp;
        Research.
      </p>
    </section>
  )
}
