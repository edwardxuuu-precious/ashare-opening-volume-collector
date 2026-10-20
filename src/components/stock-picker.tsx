'use client';
import { useMemo, useState } from 'react';
import { X } from 'lucide-react';
import {
  Combobox,
  ComboboxContent,
  ComboboxList,
  ComboboxItem,
  ComboboxEmpty,
  ComboboxChips,
  ComboboxChip,
  ComboboxChipsInput,
  useComboboxAnchor,
} from '@/components/ui/combobox';

export type StockOption = { code: string; name: string };
export function StockPicker({
  stocks,
  value,
  multiple,
  onChange,
}: {
  stocks: StockOption[];
  value: string[];
  multiple: boolean;
  onChange: (codes: string[]) => void;
}) {
  const [search, setSearch] = useState('');
  const anchor = useComboboxAnchor();
  const names = useMemo(
    () => new Map(stocks.map((s) => [s.code, s.name])),
    [stocks],
  );
  const label = (code: string) => `${names.get(code) ?? '名单外'} ${code}`;
  const matches = useMemo(
    () =>
      stocks
        .filter((s) =>
          `${s.code} ${s.name}`
            .toLowerCase()
            .includes(search.trim().toLowerCase()),
        )
        .slice(0, 40)
        .map((s) => s.code),
    [stocks, search],
  );
  return (
    <div className="stock-selection">
      <Combobox
        multiple
        items={stocks.map((s) => s.code)}
        filteredItems={matches}
        value={value}
        inputValue={search}
        onInputValueChange={setSearch}
        itemToStringLabel={label}
        onValueChange={(codes) => {
          onChange(multiple ? codes : codes.slice(-1));
          setSearch('');
        }}
      >
        <ComboboxChips ref={anchor}>
          {(multiple ? value : []).map((code) => (
            <ComboboxChip key={code} removeLabel={`移除 ${label(code)}`}>
              {label(code)}
            </ComboboxChip>
          ))}
          <ComboboxChipsInput
            id="stock-picker"
            aria-label={multiple ? '选择多个股票' : '选择一个股票'}
            placeholder={
              value.length
                ? multiple
                  ? '添加股票'
                  : '切换股票'
                : '名称 / 代码'
            }
          />
          {!multiple && value.length > 0 && (
            <button
              type="button"
              className="clear-stock"
              onClick={() => onChange([])}
              aria-label="清除所选股票"
              title="清除股票"
            >
              <X size={13} aria-hidden="true" />
            </button>
          )}
        </ComboboxChips>
        <ComboboxContent anchor={anchor}>
          <ComboboxEmpty>未找到股票</ComboboxEmpty>
          <ComboboxList>
            {(code: string) => (
              <ComboboxItem key={code} value={code}>
                {label(code)}
              </ComboboxItem>
            )}
          </ComboboxList>
        </ComboboxContent>
      </Combobox>
    </div>
  );
}
