using Google.Protobuf;

namespace Kurrent.Agent.Schema;

/// <summary>
/// Sanctioned JSON entry point for canonical events. Direct calls to
/// <see cref="JsonFormatter"/> / <see cref="JsonParser"/> are forbidden
/// outside this class — the snake_case wire format depends on the
/// <c>preserveProtoFieldNames=true</c> flag set here.
/// </summary>
public static class SchemaJsonOptions {
    static readonly JsonFormatter Formatter = new(
        new JsonFormatter.Settings(formatDefaultValues: false)
            .WithPreserveProtoFieldNames(true)
            .WithFormatEnumsAsIntegers(false));

    static readonly JsonParser Parser = new(
        JsonParser.Settings.Default.WithIgnoreUnknownFields(true));

    /// <summary>Serialise a canonical event to its JSON wire form.</summary>
    public static string ToJson(IMessage message) => Formatter.Format(message);

    /// <summary>Parse a JSON wire payload into the given canonical event type.</summary>
    public static T FromJson<T>(string json) where T : IMessage<T>, new() =>
        Parser.Parse<T>(json);
}
