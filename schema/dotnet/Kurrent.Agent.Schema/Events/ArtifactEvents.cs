using System.Text.Json;

namespace Kurrent.Agent.Schema.Events;

/// <summary>
/// Binary artifact version. Exactly one of <see cref="InlineBytes"/> /
/// <see cref="CanonicalUri"/> is expected. See SCHEMA_v2 §3.7.
/// </summary>
public sealed record ArtifactVersionCreated(
    int                                        Version,
    string?                                    MimeType,
    byte[]?                                    InlineBytes,
    string?                                    CanonicalUri,
    IReadOnlyDictionary<string, JsonElement>   CustomMetadata,
    DateTimeOffset                             CreatedAt,
    IReadOnlyDictionary<string, JsonElement>?  Extensions = null
);
