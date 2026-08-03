/**
 * Root component and route table.
 *
 * The routes are fixed by `docs/architecture.md` section 6. The pages behind them are
 * scaffold placeholders; each is filled in by the issue that owns it.
 */

import { Route, Routes } from 'react-router-dom'

import Header from '@/components/Header'
import About from '@/pages/About'
import AllCases from '@/pages/AllCases'
import CaseDetail from '@/pages/CaseDetail'
import Contact from '@/pages/Contact'
import Faq from '@/pages/Faq'
import Home from '@/pages/Home'
import Methodology from '@/pages/Methodology'
import NotFound from '@/pages/NotFound'

export default function App(): JSX.Element {
  return (
    <div className="flex min-h-screen flex-col">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <Header />
      <main id="main" className="mx-auto w-full max-w-content flex-1 px-4 py-8">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/cases" element={<AllCases />} />
          <Route path="/cases/:jurisdiction/:sourceId" element={<CaseDetail />} />
          <Route path="/about" element={<About />} />
          <Route path="/methodology" element={<Methodology />} />
          <Route path="/faq" element={<Faq />} />
          <Route path="/contact" element={<Contact />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
    </div>
  )
}
