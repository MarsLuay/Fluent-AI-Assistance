using System;
using System.IO;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;

internal static class TecanChecksumShim
{
    private const BindingFlags AnyInstance =
        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    private static int Main(string[] args)
    {
        if (args.Length != 1)
        {
            Console.Error.WriteLine("usage: tecan_checksum_shim <Tecan.Core.Util.dll>");
            return 2;
        }

        try
        {
            byte[] original = ReadAllBytes(Console.OpenStandardInput());
            bool hasBom =
                original.Length >= 3
                && original[0] == 0xEF
                && original[1] == 0xBB
                && original[2] == 0xBF;
            int offset = hasBom ? 3 : 0;
            string xml = Encoding.UTF8.GetString(original, offset, original.Length - offset);

            Assembly assembly = Assembly.LoadFrom(Path.GetFullPath(args[0]));
            Type type = assembly.GetType("Tecan.Core.Util.Xml.ChecksumHandler", true);
            ConstructorInfo constructor = type.GetConstructors(AnyInstance)[0];
            object handler = constructor.Invoke(new object[] { "Payload", "Checksum" });
            MethodInfo compute = type.GetMethod("GetChecksumForXmlElement", AnyInstance);

            var document = new XmlDocument { PreserveWhitespace = true };
            document.LoadXml(xml);
            XmlElement payload =
                (XmlElement)document.DocumentElement.SelectSingleNode("Payload");
            string checksum = (string)compute.Invoke(handler, new object[] { payload });

            string rewritten = Regex.Replace(
                xml,
                "(<Checksum>).*?(</Checksum>)",
                match => match.Groups[1].Value + checksum + match.Groups[2].Value,
                RegexOptions.Singleline,
                TimeSpan.FromSeconds(5)
            );
            if (rewritten == xml && !xml.Contains("<Checksum>" + checksum + "</Checksum>"))
            {
                Console.Error.WriteLine("checksum element was not replaced");
                return 1;
            }

            Stream output = Console.OpenStandardOutput();
            if (hasBom)
            {
                byte[] bom = Encoding.UTF8.GetPreamble();
                output.Write(bom, 0, bom.Length);
            }
            byte[] encoded = new UTF8Encoding(false).GetBytes(rewritten);
            output.Write(encoded, 0, encoded.Length);
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception);
            return 1;
        }
    }

    private static byte[] ReadAllBytes(Stream input)
    {
        using (var memory = new MemoryStream())
        {
            input.CopyTo(memory);
            return memory.ToArray();
        }
    }
}
