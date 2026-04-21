using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Kurrent.Agent.Schema;

/// <summary>
/// Shared <see cref="JsonSerializerOptions"/> for canonical event models.
///
/// - Snake-case property names (matches the Python mirror).
/// - Null-valued properties omitted on write (fixture and wire compactness).
/// - Unknown members skipped on read (forward-compatibility).
/// - <see cref="DateTimeOffset"/> serialised with <c>Z</c> suffix for UTC.
/// </summary>
public static class SchemaJsonOptions {
    public static JsonSerializerOptions Default { get; } = Build();

    static JsonSerializerOptions Build() {
        var opts = new JsonSerializerOptions {
            PropertyNamingPolicy    = JsonNamingPolicy.SnakeCaseLower,
            DictionaryKeyPolicy     = JsonNamingPolicy.SnakeCaseLower,
            DefaultIgnoreCondition  = JsonIgnoreCondition.WhenWritingNull,
            UnmappedMemberHandling  = JsonUnmappedMemberHandling.Skip,
            WriteIndented           = false,
        };
        opts.Converters.Add(new IsoDateTimeOffsetConverter());
        return opts;
    }
}

/// <summary>
/// Serialises UTC <see cref="DateTimeOffset"/> values with a <c>Z</c> suffix
/// (matching Python / ISO-8601 canonical form), non-UTC values with
/// <c>±HH:mm</c>. Reads accept both forms.
/// </summary>
sealed class IsoDateTimeOffsetConverter : JsonConverter<DateTimeOffset> {
    public override DateTimeOffset Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options) {
        var s = reader.GetString()
                ?? throw new JsonException("Expected ISO-8601 datetime string.");
        return DateTimeOffset.Parse(s, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal);
    }

    public override void Write(Utf8JsonWriter writer, DateTimeOffset value, JsonSerializerOptions options) {
        var formatted = value.Offset.Ticks == 0
            ? value.UtcDateTime.ToString("yyyy-MM-ddTHH:mm:ss.FFFFFFF", CultureInfo.InvariantCulture) + "Z"
            : value.ToString("yyyy-MM-ddTHH:mm:ss.FFFFFFFzzz", CultureInfo.InvariantCulture);
        writer.WriteStringValue(formatted);
    }
}
