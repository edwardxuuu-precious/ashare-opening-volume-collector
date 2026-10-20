import Image from 'next/image';

export const BRAND_NAME = '量序';

export function BrandIdentity() {
  return (
    <>
      <Image
        className="brand-mark"
        src="/brand/liangxu-symbol.svg"
        width={36}
        height={36}
        alt=""
        aria-hidden="true"
        unoptimized
      />
      <span className="brand-name">{BRAND_NAME}</span>
    </>
  );
}
