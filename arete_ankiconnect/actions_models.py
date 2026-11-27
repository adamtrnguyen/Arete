"""Note-type (model), field, and template AnkiConnect actions."""

import re

from anki.consts import MODEL_CLOZE

from . import util


class ModelActions:
    """Note type inspection and schema mutation actions."""

    @util.api()
    def modelNames(self):
        # What models.allNames() returns, without its two deprecation warnings.
        names = self.collection().models.all_names_and_ids()
        return [name.name for name in names]

    @util.api()
    def createModel(self, modelName, inOrderFields, cardTemplates, css=None, isCloze=False):
        if len(inOrderFields) == 0:
            raise Exception("Must provide at least one field for inOrderFields")
        if len(cardTemplates) == 0:
            raise Exception("Must provide at least one card for cardTemplates")
        if modelName in self.modelNames():
            raise Exception("Model name already exists")

        mm = self.collection().models
        m = mm.new(modelName)
        if isCloze:
            m["type"] = MODEL_CLOZE

        for field in inOrderFields:
            fm = mm.new_field(field)
            mm.add_field(m, fm)

        if css is not None:
            m["css"] = css

        for cardCount, card in enumerate(cardTemplates, start=1):
            cardName = card.get("Name", f"Card {cardCount}")
            t = mm.new_template(cardName)
            t["qfmt"] = card["Front"]
            t["afmt"] = card["Back"]
            mm.add_template(m, t)

        mm.add(m)
        self.scheduleRefresh()
        return m

    @util.api()
    def modelNamesAndIds(self):
        return {
            model: int(self.collection().models.by_name(model)["id"]) for model in self.modelNames()
        }

    @util.api()
    def modelNameFromId(self, modelId):
        model = self.collection().models.get(modelId)
        if model is None:
            raise Exception(f"model was not found: {modelId}")
        return model["name"]

    @util.api()
    def modelFieldNames(self, modelName):
        model = self.getModel(modelName)
        return [field["name"] for field in model["flds"]]

    @util.api()
    def modelFieldDescriptions(self, modelName):
        model = self.getModel(modelName)
        return [field["description"] for field in model["flds"]]

    @util.api()
    def modelFieldFonts(self, modelName):
        model = self.getModel(modelName)
        return {
            field["name"]: {"font": field["font"], "size": field["size"]} for field in model["flds"]
        }

    @util.api()
    def modelFieldsOnTemplates(self, modelName):
        model = self.getModel(modelName)
        templates = {}
        for template in model["tmpls"]:
            fields = []
            for side in ["qfmt", "afmt"]:
                fieldsForSide = []
                # based on _fieldsOnTemplate from aqt/clayout.py
                matches = re.findall("{{[^#/}]+?}}", template[side])
                for match in matches:
                    match = re.sub(r"[{}]", "", match)
                    match = match.split(":")[-1]
                    if match == "FrontSide" or (side == "afmt" and match in fields[0]):
                        continue
                    fieldsForSide.append(match)
                fields.append(fieldsForSide)
            templates[template["name"]] = fields
        return templates

    @util.api()
    def modelTemplates(self, modelName):
        model = self.getModel(modelName)
        return {
            template["name"]: {"Front": template["qfmt"], "Back": template["afmt"]}
            for template in model["tmpls"]
        }

    @util.api()
    def modelStyling(self, modelName):
        model = self.getModel(modelName)
        return {"css": model["css"]}

    @util.api()
    def updateModelTemplates(self, model):
        models = self.collection().models
        ankiModel = self.getModel(model["name"])

        templates = model["templates"]
        for ankiTemplate in ankiModel["tmpls"]:
            template = templates.get(ankiTemplate["name"])
            if template:
                qfmt = template.get("Front")
                if qfmt:
                    ankiTemplate["qfmt"] = qfmt
                afmt = template.get("Back")
                if afmt:
                    ankiTemplate["afmt"] = afmt

        models.update_dict(ankiModel)
        self.scheduleRefresh()

    @util.api()
    def updateModelStyling(self, model):
        models = self.collection().models
        ankiModel = self.getModel(model["name"])
        ankiModel["css"] = model["css"]
        models.update_dict(ankiModel)
        self.scheduleRefresh()

    @util.api()
    def findAndReplaceInModels(
        self, modelName, findText, replaceText, front=True, back=True, css=True
    ):
        if not modelName:
            ankiModelNames = self.modelNames()
        else:
            self.getModel(modelName)
            ankiModelNames = [modelName]

        updatedModels = 0
        models = self.collection().models
        for name in ankiModelNames:
            model = models.by_name(name)
            checkForText = False
            if css and findText in model["css"]:
                checkForText = True
                model["css"] = model["css"].replace(findText, replaceText)
            for tmpls in model.get("tmpls"):
                if front and findText in tmpls["qfmt"]:
                    checkForText = True
                    tmpls["qfmt"] = tmpls["qfmt"].replace(findText, replaceText)
                if back and findText in tmpls["afmt"]:
                    checkForText = True
                    tmpls["afmt"] = tmpls["afmt"].replace(findText, replaceText)
            if checkForText:
                models.update_dict(model)
                updatedModels += 1
        if updatedModels:
            self.scheduleRefresh()
        return updatedModels

    @util.api()
    def modelTemplateRename(self, modelName, oldTemplateName, newTemplateName):
        mm = self.collection().models
        model = self.getModel(modelName)
        ankiTemplate = self.getTemplate(model, oldTemplateName)
        ankiTemplate["name"] = newTemplateName
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelTemplateReposition(self, modelName, templateName, index):
        mm = self.collection().models
        model = self.getModel(modelName)
        ankiTemplate = self.getTemplate(model, templateName)
        mm.reposition_template(model, ankiTemplate, index)
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelTemplateAdd(self, modelName, template):
        mm = self.collection().models
        model = self.getModel(modelName)
        name = template["Name"]
        qfmt = template["Front"]
        afmt = template["Back"]

        for ankiTemplate in model["tmpls"]:
            if ankiTemplate["name"] == name:
                ankiTemplate["qfmt"] = qfmt
                ankiTemplate["afmt"] = afmt
                mm.update_dict(model)
                self.scheduleRefresh()
                return

        ankiTemplate = mm.new_template(name)
        ankiTemplate["qfmt"] = qfmt
        ankiTemplate["afmt"] = afmt
        mm.add_template(model, ankiTemplate)
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelTemplateRemove(self, modelName, templateName):
        mm = self.collection().models
        model = self.getModel(modelName)
        ankiTemplate = self.getTemplate(model, templateName)
        mm.remove_template(model, ankiTemplate)
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelFieldRename(self, modelName, oldFieldName, newFieldName):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, oldFieldName)
        mm.rename_field(model, field, newFieldName)
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelFieldReposition(self, modelName, fieldName, index):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)
        mm.reposition_field(model, field, index)
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelFieldAdd(self, modelName, fieldName, index=None):
        mm = self.collection().models
        model = self.getModel(modelName)

        modified = False
        fieldMap = mm.field_map(model)
        if fieldName not in fieldMap:
            field = mm.new_field(fieldName)
            mm.add_field(model, field)
            modified = True

        if index is not None:
            fieldMap = mm.field_map(model)
            newField = fieldMap[fieldName][1]
            mm.reposition_field(model, newField, index)
            modified = True

        if modified:
            mm.update_dict(model)
            self.scheduleRefresh()

    @util.api()
    def modelFieldRemove(self, modelName, fieldName):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)
        mm.remove_field(model, field)
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelFieldSetFont(self, modelName, fieldName, font):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)
        if not isinstance(font, str):
            raise Exception(f"font should be a string: {font}")
        field["font"] = font
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelFieldSetFontSize(self, modelName, fieldName, fontSize):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)
        if not isinstance(fontSize, int):
            raise Exception(f"fontSize should be an integer: {fontSize}")
        field["size"] = fontSize
        mm.update_dict(model)
        self.scheduleRefresh()

    @util.api()
    def modelFieldSetDescription(self, modelName, fieldName, description):
        mm = self.collection().models
        model = self.getModel(modelName)
        field = self.getField(model, fieldName)
        if not isinstance(description, str):
            raise Exception(f"description should be a string: {description}")
        field["description"] = description
        mm.update_dict(model)
        self.scheduleRefresh()
        return True
