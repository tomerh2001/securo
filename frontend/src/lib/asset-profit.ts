import type { Asset } from '@/types'

type ProfitAsset = Pick<
  Asset,
  | 'gain_loss'
  | 'purchase_price'
  | 'realized_gain'
  | 'sell_date'
  | 'sell_price'
  | 'total_invested'
  | 'value_count'
> & Pick<Asset, 'investment_details'>

export function getAssetProfit(asset: ProfitAsset) {
  // Provider balances do not establish the contributions and cost basis needed
  // for investment returns. Keep balance changes in the value history instead.
  if (asset.investment_details) return null
  const amount = asset.sell_date
    ? asset.realized_gain ?? (
      asset.sell_price != null && asset.purchase_price != null
        ? asset.sell_price - asset.purchase_price
        : null
    )
    : asset.value_count > 0 ? asset.gain_loss : null

  if (amount == null) return null

  const cost = asset.total_invested ?? asset.purchase_price
  return {
    amount,
    percentage: cost ? (amount / cost) * 100 : null,
  }
}
