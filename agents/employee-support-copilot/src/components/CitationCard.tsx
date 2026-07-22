"use client";

import type { CitationDTO } from "../types/chat";

interface Props {
  citation: CitationDTO;
  index: number;
}

export default function CitationCard({ citation, index }: Props) {
  const scoreDisplay =
    citation.score !== null && citation.score !== undefined
      ? `${(citation.score * 100).toFixed(0)}%`
      : null;

  return (
    <div className="flex gap-2 rounded-lg border border-gray-100 bg-gray-50 p-2.5 text-xs">
      {/* Number */}
      <div className="flex-shrink-0 w-5 h-5 rounded-full bg-gray-200 flex items-center justify-center text-gray-600 font-semibold text-[10px]">
        {index}
      </div>

      <div className="flex-1 min-w-0">
        {/* Title + domain badge */}
        <div className="flex items-center gap-1.5 mb-1 flex-wrap">
          <span className="font-semibold text-gray-800 truncate">{citation.title}</span>
          <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-medium ${citation.badgeColor}`}>
            {citation.domainIcon} {citation.domainLabel}
          </span>
          {scoreDisplay && (
            <span className="ml-auto text-gray-400 flex-shrink-0">{scoreDisplay}</span>
          )}
        </div>

        {/* Snippet */}
        <p className="text-gray-600 leading-snug line-clamp-3">{citation.snippet}</p>

        {/* Source link / page */}
        {(citation.source || citation.page !== null) && (
          <div className="mt-1 flex items-center gap-2 text-gray-400">
            {citation.source && (
              citation.source.startsWith("http") ? (
                <a
                  href={citation.source}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="hover:text-blue-600 underline truncate max-w-xs"
                >
                  {citation.source}
                </a>
              ) : (
                <span className="truncate">{citation.source}</span>
              )
            )}
            {citation.page !== null && citation.page !== undefined && (
              <span className="flex-shrink-0">p.{citation.page}</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
