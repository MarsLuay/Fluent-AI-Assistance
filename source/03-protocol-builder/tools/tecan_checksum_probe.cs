using System;
using System.IO;
using System.Reflection;
using System.Xml;

internal static class TecanChecksumProbe
{
    private const BindingFlags AnyInstance =
        BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    private static int Main(string[] args)
    {
        if (args.Length != 2 && args.Length != 3)
        {
            Console.Error.WriteLine(
                "usage: tecan_checksum_probe <assembly> <xml-file> [rewritten-xml-file]"
            );
            return 2;
        }

        try
        {
            Assembly assembly = Assembly.LoadFrom(Path.GetFullPath(args[0]));
            Type type = assembly.GetType("Tecan.Core.Util.Xml.ChecksumHandler", true);
            ConstructorInfo constructor = type.GetConstructors(AnyInstance)[0];
            MethodInfo calculate = type.GetMethod("CalculateNewChecksum", AnyInstance);

            if (args.Length == 3)
            {
                object rewriteHandler = constructor.Invoke(
                    new object[] { "Payload", "Checksum" }
                );
                var rewriteDocument = new XmlDocument { PreserveWhitespace = true };
                rewriteDocument.Load(Path.GetFullPath(args[1]));
                calculate.Invoke(
                    rewriteHandler,
                    new object[] { rewriteDocument.DocumentElement }
                );
                rewriteDocument.Save(Path.GetFullPath(args[2]));
            }

            foreach (bool preserveWhitespace in new[] { true, false })
            {
                object handler = constructor.Invoke(new object[] { "Payload", "Checksum" });
                var document = new XmlDocument { PreserveWhitespace = preserveWhitespace };
                document.Load(Path.GetFullPath(args[1]));
                string stored = document.SelectSingleNode("//Checksum").InnerText;
                XmlElement payload = (XmlElement)document.DocumentElement.SelectSingleNode("Payload");
                MethodInfo validate = type.GetMethod(
                    "IsCheckSumValid",
                    AnyInstance,
                    null,
                    new[] { typeof(XmlElement), typeof(string) },
                    null
                );
                bool valid = (bool)validate.Invoke(handler, new object[] { payload, stored });

                document.Load(Path.GetFullPath(args[1]));
                payload = (XmlElement)document.DocumentElement.SelectSingleNode("Payload");
                MethodInfo computePayload = type.GetMethod(
                    "GetChecksumForXmlElement",
                    AnyInstance
                );
                string payloadChecksum = (string)computePayload.Invoke(
                    handler,
                    new object[] { payload }
                );

                document.Load(Path.GetFullPath(args[1]));
                object result = calculate.Invoke(handler, new object[] { document.DocumentElement });

                Console.WriteLine(
                    "preserveWhitespace=" + preserveWhitespace
                    + " stored=" + stored
                    + " valid=" + valid
                    + " payloadChecksum=" + payloadChecksum
                    + " result=" + result
                    + " checksum=" + document.SelectSingleNode("//Checksum").InnerText
                );
            }
            return 0;
        }
        catch (Exception exception)
        {
            Console.Error.WriteLine(exception);
            return 1;
        }
    }
}
