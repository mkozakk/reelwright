# 0005: S3 Standard storage everywhere, no Glacier/IA tiering

## Status

Accepted .

## Context

S3 Infrequent Access and Glacier both look like the obvious cost lever for a pipeline that stores media: cheaper per-GB storage for data that isn't accessed often. The catch is the minimum-storage-duration charge each tier imposes: IA bills for at least 30 days of storage even if an object is deleted sooner, Glacier for at least 90+. Every bucket in this pipeline expires its objects well inside those windows: raw uploads at 48 hours, work artifacts at 7 days, outputs at 30 days.

## Decision

Every bucket stays on S3 Standard. No lifecycle transition rules to IA or Glacier anywhere in the pipeline.

## Consequences

- Simpler lifecycle configuration: each bucket has one rule (expire after N days), not a transition-then-expire chain.
- Tiering would cost more than Standard here, not less, because the minimum-storage-duration charge would apply to objects that are deleted well before that minimum is reached. Adding IA/Glacier under these lifecycles would be a net cost increase, not a saving.
- This is a decision about *this* pipeline's object lifetimes specifically, not a general claim that tiering doesn't work. If any bucket's retention window ever grows past the 30/90-day minimums (a longer output retention tier, for instance), this decision should be revisited for that bucket rather than assumed to still hold.
