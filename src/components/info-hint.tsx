'use client';

import type { ReactNode } from 'react';
import { Info } from 'lucide-react';
import {
  Popover,
  PopoverContent,
  PopoverTitle,
  PopoverTrigger,
} from '@/components/ui/popover';

/** Hover on desktop; click, tap or Enter keeps the same explanation available. */
export function InfoHint({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <Popover>
      <PopoverTrigger
        className="info-trigger"
        aria-label={label}
        openOnHover
        delay={250}
      >
        <Info size={14} aria-hidden="true" />
      </PopoverTrigger>
      <PopoverContent className="info-content" align="start" sideOffset={8}>
        <PopoverTitle>{label}</PopoverTitle>
        <div className="info-copy">{children}</div>
      </PopoverContent>
    </Popover>
  );
}
