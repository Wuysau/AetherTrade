import type { Instrument } from './types';

// Explicit initial catalog; availability always comes from the quote service.
export const INSTRUMENTS: Instrument[] = [
  { symbol: 'BTCUSDT', name: 'Bitcoin', asset_class: 'CRYPTO', base_asset: 'BTC', quote_asset: 'USDT', underlying_symbol: null },
  { symbol: 'ETHUSDT', name: 'Ethereum', asset_class: 'CRYPTO', base_asset: 'ETH', quote_asset: 'USDT', underlying_symbol: null },
  { symbol: 'SOLUSDT', name: 'Solana', asset_class: 'CRYPTO', base_asset: 'SOL', quote_asset: 'USDT', underlying_symbol: null },
  { symbol: 'TSLABUSDT', name: 'Tesla bStock', asset_class: 'BSTOCK', base_asset: 'TSLAB', quote_asset: 'USDT', underlying_symbol: 'TSLA' },
  { symbol: 'NVDABUSDT', name: 'NVIDIA bStock', asset_class: 'BSTOCK', base_asset: 'NVDAB', quote_asset: 'USDT', underlying_symbol: 'NVDA' },
  { symbol: 'AAPLBUSDT', name: 'Apple bStock', asset_class: 'BSTOCK', base_asset: 'AAPLB', quote_asset: 'USDT', underlying_symbol: 'AAPL' },
];
export function InstrumentOptions({ instruments = INSTRUMENTS }: { instruments?: Instrument[] }) {
  return <>{(['CRYPTO', 'BSTOCK'] as const).map(group => <optgroup key={group} label={group === 'CRYPTO' ? '加密现货' : 'Binance bStocks 现货'}>{instruments.filter(i => i.asset_class === group).map(i => <option value={i.symbol} key={i.symbol}>{i.symbol} · {i.name}</option>)}</optgroup>)}</>;
}
