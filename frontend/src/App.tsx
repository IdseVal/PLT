/**
 * Root component and route table.
 *
 * The routes are fixed by `docs/architecture.md` section 6. The shell around them - skip
 * link, header, main landmark, footer - is the same on every route; `/`, `/cases` and
 * `/cases/:jurisdiction/:sourceId` render placeholders that the issues owning those pages
 * replace.
 */

import { Route, Routes } from 'react-router-dom'

import Footer from '@/components/Footer'
import Header from '@/components/Header'
import About from '@/pages/About'
import AllCases from '@/pages/AllCases'
import CaseDetail from '@/pages/CaseDetail'
import ConfirmSubscription from '@/pages/ConfirmSubscription'
import Contact from '@/pages/Contact'
import Faq from '@/pages/Faq'
import Home from '@/pages/Home'
import Methodology from '@/pages/Methodology'
import NotFound from '@/pages/NotFound'
import Unsubscribe from '@/pages/Unsubscribe'

export default function App(): JSX.Element {
  return (
    <div className="flex min-h-screen flex-col">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header />
      <main id="main" className="mx-auto w-full max-w-content flex-1 px-4 py-8 sm:py-10">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/cases" element={<AllCases />} />
          <Route path="/cases/:jurisdiction/:sourceId" element={<CaseDetail />} />
          <Route path="/about" element={<About />} />
          <Route path="/methodology" element={<Methodology />} />
          <Route path="/faq" element={<Faq />} />
          <Route path="/contact" element={<Contact />} />
          {/* The two mailing-list routes. Neither is in the site menu: a reader reaches
              them from the front-page form or from a link in an email, and putting them in
              the header would clutter the four items the brief fixes. */}
          <Route path="/subscribe/confirm" element={<ConfirmSubscription />} />
          <Route path="/unsubscribe" element={<Unsubscribe />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <Footer />
    </div>
  )
}
