# Visa Checker

نظام بسيط للتحقق من حالة التأشيرات السعودية من ملفات Excel.

## ما تم إعدادُه
- تطبيق Flask في جذر المشروع: `app.py` (يستخدم القوالب في `visa_system/templates`).
- واجهة ويب جاهزة: `visa_system/templates/index.html` (واجهة عربية، رفع ملف Excel، عرض إحصائيات، تنزيل النتائج).
- نقاط النهاية الرئيسية:
  - GET / -> صفحة الواجهة
  - POST /process -> رفع ملف Excel ومعالجة أرقام الطلب
  - GET /download/<filename> -> تنزيل ملف النتائج المحفوظ في مجلد مؤقت
  - GET /stats -> حالة بسيطة للتطبيق
- ملفات النشر في جذر المستودع:
  - `Procfile` (web: gunicorn app:app)
  - `requirements.txt`
  - `runtime.txt` (python-3.11.15)
  - `.gitignore`

## المتطلبات
مثبتة في `requirements.txt`، أهمها:
- Flask
- pandas
- requests
- beautifulsoup4
- openpyxl
- gunicorn

## التشغيل محلياً
1. إنشاء بيئة افتراضية وتثبيت المتطلبات:

```bash
python -m venv venv
source venv/bin/activate   # على Windows: venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

2. تشغيل التطبيق (للاختبار):

```bash
python app.py
# أو تشغيل عبر gunicorn كما في الإنتاج:
# gunicorn app:app
```

3. فتح المتصفح على:

http://127.0.0.1:5000

4. اختبار endpoints سريعاً:

```bash
curl http://127.0.0.1:5000/stats
```

## نشر على Heroku
1. تسجيل الدخول وإنشاء التطبيق:

```bash
heroku login
heroku create <app-name>
```

2. تأكد أن `Procfile` و `requirements.txt` و `runtime.txt` في جذر المشروع، ثم دفع إلى Heroku:

```bash
git add .
git commit -m "Prepare for Heroku"
git push heroku main
```

Heroku سيضبط متغير البيئة `PORT` تلقائياً؛ لا تحتاج لتعديله.

## ملاحظات تقنية وأمنية
- المعالجة حالياً متزامنة في نفس الطلب HTTP. لملفات كبيرة أو مئات الطلبات، يوصى باستخدام مهام خلفية (RQ/Celery) أو إرسال عبر queue حتى لا يحجب التطبيق.
- تم إضافة تأخير 2 ثانية بين كل طلب خارجي لمنع الضغط على الخدمة الخارجية — راجع سياسة الاستخدام للموقع المستهدف.
- يتم حفظ نتائج Excel في مجلد نظام مؤقت (`/tmp` على لينكس) ثم عرض رابط تنزيل. الملفات المؤقتة لن تُحذف تلقائياً بعد التنزيل — يمكن إضافة آلية تنظيف لاحقاً.
- ضع آليات تحقق/مصادقة إذا كان التطبيق متاحًا للجمهور للحماية من سوء الاستخدام.

## تخصيص
- إذا رغبت، أستطيع:
  - إضافة صفحة README مفصّلة باللغة الإنجليزية.
  - إضافة عملية حذف تلقائي للملفات المؤقتة بعد وقت محدد.
  - نقل `templates` و`static` إلى جذر المشروع إذا رغبت بأن يكون `app.py` مع القوالب جنباً إلى جنب.

---

إن رغبت أن أضيف أي من التحسينات أعلاه (تنظيف مؤقت، مهام خلفية، حماية / basic auth، أو ضبط CI/CD)، قل لي أي واحدة أبدأ بها وسأجري التعديل واشتغل على commit منفصل.