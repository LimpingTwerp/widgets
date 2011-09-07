#make the program quit on Ctrl+C
import signal
signal.signal(signal.SIGINT, signal.SIG_DFL)

import os, sys, numpy, copy

from PyQt4.QtCore import pyqtSignal, QTimer, QRectF
from PyQt4.QtGui import QColor, QMainWindow, QApplication, QFileDialog, \
                        QMessageBox, qApp, QItemSelectionModel
from PyQt4 import uic

from lazyflow.graph import Graph


from lazyflow.operators import *

from volumeeditor.pixelpipeline.datasources import LazyflowSource
from volumeeditor.pixelpipeline._testing import OpDataProvider
from volumeeditor.layer import GrayscaleLayer, RGBALayer, ColortableLayer, \
                               AlphaModulatedLayer
from volumeeditor.layerstack import LayerStackModel
from volumeeditor.volumeEditor import VolumeEditor
from volumeeditor.pixelpipeline.datasources import LazyflowSinkSource

from labelListView import Label
from labelListModel import LabelListModel

from featureDlg import FeatureDlg, FeatureEntry

class Main(QMainWindow):    
    haveData        = pyqtSignal()
    dataReadyToView = pyqtSignal()
        
    def __init__(self, argv):
        QMainWindow.__init__(self)
        
        #Normalize the data if true
        self._normalize_data=True
        arguments=sys.argv
        
        if 'notnormalize' in arguments:
            self._normalize_data=False
            arguments.remove('notnormalize')
        
        
        self.opPredict = None
        self.opTrain = None
        self._colorTable16 = self._createDefault16ColorColorTable()
        
        self.g = Graph(1, 2048*1024**2*5)
        #self.g = Graph(1, 18000*1024**2)
        self.fixableOperators = []
        
        self.featureDlg=None
        
        #old ilastik:
        #self.groupScaleNames = ['Tiny', 'Small', 'Medium', 'Large', 'Huge', 'Megahuge', 'Gigahuge']
        self.featScalesList=[0.3, 0.7, 1, 1.6, 3.5, 5.0, 10.0]
        
        
        self.initUic()
        
        #
        # if the filename was specified on command line, load it
        #
        arguments=sys.argv

        if len(arguments) >= 2:
            def loadFile():
                self._openFile(sys.argv[1:])
            QTimer.singleShot(0, loadFile)
        
    def initUic(self):
        #get the absolute path of the 'ilastik' module
        uic.loadUi("designerElements/MainWindow.ui", self) 
        #connect the window and graph creation to the opening of the file
        self.actionOpen.triggered.connect(self.openFile)
        self.actionQuit.triggered.connect(qApp.quit)
        
        def toggleDebugPatches(show):
            self.editor.showDebugPatches = show
        def fitToScreen():
            shape = self.editor.posModel.shape
            for i, v in enumerate(self.editor.imageViews):
                s = list(copy.copy(shape))
                del s[i]
                v.changeViewPort(v.scene().data2scene.mapRect(QRectF(0,0,*s)))  
        
        self.actionShowDebugPatches.toggled.connect(toggleDebugPatches)
        self.actionFitToScreen.triggered.connect(fitToScreen)
        
        self.haveData.connect(self.initGraph)
        self.dataReadyToView.connect(self.initEditor)
        
        self.layerstack = LayerStackModel()
        
        model = LabelListModel()
        self.labelListView.setModel(model)
        self.labelListModel=model
        
        self.labelListModel.rowsAboutToBeRemoved.connect(self.onLabelAboutToBeRemoved)
        self.labelListModel.labelSelected.connect(self.switchLabel)
        
        def onDataChanged(topLeft, bottomRight):
            firstRow = topLeft.row()
            lastRow  = bottomRight.row()
            assert firstRow == lastRow
            firstCol = topLeft.column()
            lastCol  = bottomRight.column()
            if 0 in range(firstCol, lastCol+1):
                self.switchColor(firstRow+1, self.labelListModel[firstRow].color)
                self.editor.scheduleSlicesRedraw()
            #self.onColorChanged()
            
            
        self.labelListModel.dataChanged.connect(onDataChanged)
        
        self.AddLabelButton.clicked.connect(self.addLabel)
        
        self.SelectFeaturesButton.clicked.connect(self.onFeatureButtonClicked)
        self.StartClassificationButton.clicked.connect(self.startClassification)
        
        self.StartClassificationButton.setEnabled(True)
        
        self.checkInteractive.toggled.connect(self.toggleInteractive)   
        self.initTheFeatureDlg()
        
    def toggleInteractive(self, checked):
        print "checked = ", checked
        
        #Check if the number of labels in the layer stack is equals to the number of Painted labels
        if checked==True:
            labels =numpy.unique(numpy.asarray(self.opLabels.outputs["nonzeroValues"][:].allocate().wait()[0]))           
            nPaintedLabels=labels.shape[0]
            nLabelsLayers = self.labelListModel.rowCount()
            selectedFeatures = numpy.asarray(self.featureDlg.featureTableWidget.createSelectedFeaturesBoolMatrix())
            
            if nPaintedLabels!=nLabelsLayers:
                self.checkInteractive.setCheckState(0)
                mexBox=QMessageBox()
                mexBox.setText("Did you forget to paint some labels?")
                mexBox.setInformativeText("Painted Labels %d \nNumber Active Labels Layers %d"%(nPaintedLabels,self.labelListModel.rowCount()))
                mexBox.exec_()
                #print "Painted Labels: ", labels
                #print "nPainted Labels: " , nPaintedLabels
                #print "nLabelsLayers", self.labelListModel.rowCount()
                return
            if (selectedFeatures==0).all():
                self.checkInteractive.setCheckState(0)
                mexBox=QMessageBox()
                mexBox.setText("The are no features selected ")
                mexBox.exec_()
                return
                
        
        if checked==True:
            self.AddLabelButton.setEnabled(False)
            self.SelectFeaturesButton.setEnabled(False)
            for o in self.fixableOperators:
                o.inputs["fixAtCurrent"].setValue(False)
        else:
            self.AddLabelButton.setEnabled(True)
            self.SelectFeaturesButton.setEnabled(True)
            for o in self.fixableOperators:
                o.inputs["fixAtCurrent"].setValue(True)
                
        self.editor.scheduleSlicesRedraw()
        
    def switchLabel(self, row):
        print "switching to label=%r" % (self.labelListModel[row])
        #+1 because first is transparent
        
        #FIXME: shouldn't be just row+1 here
        self.editor.brushingModel.setDrawnNumber(row+1)
        self.editor.brushingModel.setBrushColor(self.labelListModel[row].color)
        
    def switchColor(self, row, color):
        print "label=%d changes color to %r" % (row, color)
        self.labellayer.colorTable[row]=color.rgba()
        self.editor.brushingModel.setBrushColor(color)
        self.editor.scheduleSlicesRedraw()
    
    def addLabel(self):
        color = QColor(numpy.random.randint(0,255), numpy.random.randint(0,255), numpy.random.randint(0,255))
        numLabels = len(self.labelListModel)
        if numLabels < len(self._colorTable16):
            color = self._colorTable16[numLabels]
        self.labellayer.colorTable.append(color.rgba())
        
        self.labelListModel.insertRow(self.labelListModel.rowCount(), Label("Label %d" % (self.labelListModel.rowCount() + 1), color))
        nlabels = self.labelListModel.rowCount()
        if self.opPredict is not None:
            print "Label added, changing predictions"
            #re-train the forest now that we have more labels
            #self.opTrain.notifyDirty(None, None)
            self.opPredict.inputs['LabelsCount'].setValue(nlabels)
            self.addPredictionLayer(nlabels-1, self.labelListModel._labels[nlabels-1])
        
        #make the new label selected
        index = self.labelListModel.index(nlabels-1, 1)
        self.labelListModel._selectionModel.select(index, QItemSelectionModel.ClearAndSelect)
        
        
        #FIXME: this should watch for model changes   
        #drawing will be enabled when the first label is added  
        self.editor.setDrawingEnabled(True)
    
    def onLabelAboutToBeRemoved(self, parent, start, end):
        #the user deleted a label, reshape prediction and remove the layer
        #the interface only allows to remove one label at a time?
        
        nout = start-end+1
        ncurrent = self.labelListModel.rowCount()
        print "removing", nout, "out of ", ncurrent
        
        if self.opPredict is not None:
            self.opPredict.inputs['LabelsCount'].setValue(ncurrent-nout)
        for il in range(start, end+1):
            labelvalue = self.labelListModel._labels[il]
            self.removePredictionLayer(labelvalue)
            self.opLabels.inputs["deleteLabel"].setValue(il+1)
            self.editor.scheduleSlicesRedraw()
            
    
    def startClassification(self):
        if self.opTrain is None:
            #initialize all classification operators
            print "initializing classification..."
            opMultiL = Op5ToMulti(self.g)    
            opMultiL.inputs["Input0"].connect(self.opLabels.outputs["Output"])
            
            opMultiLblocks = Op5ToMulti(self.g)
            opMultiLblocks.inputs["Input0"].connect(self.opLabels.outputs["nonzeroBlocks"])
            #self.opTrain = OpTrainRandomForest(self.g)
            self.opTrain = OpTrainRandomForestBlocked(self.g)
            self.opTrain.inputs['Labels'].connect(opMultiL.outputs["Outputs"])
            
            opMultiFeaturesImages = Op5ToMulti(self.g)
            opMultiFeaturesImages.inputs["Input0"].connect(self.opPF.outputs["Output"])
            
            self.opTrain.inputs['Images'].connect( opMultiFeaturesImages.outputs["Outputs"])
            self.opTrain.inputs["nonzeroLabelBlocks"].connect(opMultiLblocks.outputs["Outputs"])
            self.opTrain.inputs['fixClassifier'].setValue(False)                
            
            opClassifierCache = OpArrayCache(self.g)
            opClassifierCache.inputs["Input"].connect(self.opTrain.outputs['Classifier'])
           
            ################## Prediction
            self.opPredict=OpPredictRandomForest(self.g)
            nclasses = self.labelListModel.rowCount()
            self.opPredict.inputs['LabelsCount'].setValue(nclasses)
            self.opPredict.inputs['Classifier'].connect(opClassifierCache.outputs['Output']) 
            self.opPredict.inputs['Image'].connect(opMultiFeaturesImages.outputs["Outputs"])

            pCache = OpBlockedArrayCache(self.g)
            pCache.inputs["fixAtCurrent"].setValue(False)
            pCache.inputs["innerBlockShape"].setValue((1,8,8,8,1))
            pCache.inputs["outerBlockShape"].setValue((1,64,64,64,1))
            pCache.inputs["Input"].connect(self.opPredict.outputs["PMaps"])
            self.pCache = pCache
  
            #add prediction results for all classes as separate channels
            for icl in range(nclasses):
                self.addPredictionLayer(icl, self.labelListModel._labels[icl])
                
            #self.updatePredictionLayers()
                                    
    def addPredictionLayer(self, icl, ref_label):
        
        selector=OpSingleChannelSelector(self.g)
        selector.inputs["Input"].connect(self.pCache.outputs['Output'])
        selector.inputs["Index"].setValue(icl)
                
        if self.checkInteractive.isChecked():
            self.pCache.inputs["fixAtCurrent"].setValue(False)
        else:
            self.pCache.inputs["fixAtCurrent"].setValue(True)
        
        predictsrc = LazyflowSource(selector.outputs["Output"][0])
        
        predictLayer = AlphaModulatedLayer(predictsrc, tintColor=ref_label.color, normalize = (0.0,1.0) )
        
        def setLayerColor(c):
            print "as the color of label '%s' has changed, setting layer's '%s' tint color to %r" % (ref_label.name, predictLayer.name, c)
            predictLayer.tintColor = c
        ref_label.colorChanged.connect(setLayerColor)
        def setLayerName(n):
            newName = "Prediction for %s" % ref_label.name
            print "as the name of label '%s' has changed, setting layer's '%s' name to '%s'" % (ref_label.name, predictLayer.name, newName)
            predictLayer.name = newName
        setLayerName(ref_label.name)
        ref_label.nameChanged.connect(setLayerName)
        
        predictLayer.ref_object = ref_label
        #make sure that labels (index = 0) stay on top!
        self.layerstack.insert(1, predictLayer )
        self.fixableOperators.append(self.pCache)
               
    def removePredictionLayer(self, ref_label):
        for il, layer in enumerate(self.layerstack):
            if layer.ref_object==ref_label:
                print "found the prediction", layer.ref_object, ref_label
                self.layerstack.removeRows(il, 1)
                break
    
    def openFile(self):
        #FIXME: only take one file for now, more to come
        #fileName = QFileDialog.getOpenFileName(self, "Open Image", os.path.abspath(__file__), "Image Files (*.png *.jpg *.bmp *.tif *.tiff *.gif *.h5)")
        fileNames = QFileDialog.getOpenFileNames(self, "Open Image", os.path.abspath(__file__), "Numpy and h5 files (*.npy *.h5)")
        self._openFile(fileNames)
        
    def _openFile(self, fileNames):
        self.inputProvider = None
        fName, fExt = os.path.splitext(str(fileNames[0]))
        print fName, fExt
        if fExt=='.npy':
            fileName = fileNames[0]
            if len(fileNames)>1:
                print "WARNING: only the first file will be read, multiple file prediction not supported yet"
            fName, fExt = os.path.splitext(str(fileName))
            self.raw = numpy.load(str(fileName))
            self.min, self.max = numpy.min(self.raw), numpy.max(self.raw)
            self.inputProvider = OpArrayPiper(self.g)
            self.inputProvider.inputs["Input"].setValue(self.raw)
            import copy,vigra
            self.inputProvider.outputs["Output"].axistags=copy.copy(vigra.defaultAxistags('txyzc'))
        elif fExt=='.h5':
            readerNew=OpH5ReaderSmoothedDataset(self.g)
            readerCache = OpBlockedArrayCache(self.g)
            readerNew.inputs["Filename"].setValue(fileNames[0])
            readerNew.inputs["hdf5Path"].setValue("volume")
            #Reader=OpH5Reader(self.g)
            #print str(fileName),'*+++++++++++++++++++++++++'
            #Reader.inputs["Filename"].setValue(str(fileName))
            #Reader.inputs["hdf5Path"].setValue("volume/data")
            self.inputProvider = OpArrayPiper(self.g)
            self.readerOut = OpArrayPiper(self.g)
            self.readerSigma = OpArrayPiper(self.g)
            
#            readerCache.inputs["fixAtCurrent"].setValue(False)
#            readerCache.inputs["innerBlockShape"].setValue((1,8,8,8,1))
#            readerCache.inputs["outerBlockShape"].setValue((1,64,64,64,1))
#            readerCache.inputs["Input"].connect(readerNew.outputs["Output"])
            self.readerOut.inputs["Input"].connect(readerNew.outputs["Outputs"])
            self.readerSigma.inputs["Input"].connect(readerNew.outputs["Sigmas"])
            self.inputProvider.inputs["Input"].connect(readerNew.outputs["Outputs"][0])
        else:
            print "not supported yet"
            return
        
        self.haveData.emit()
       
    def initGraph(self):
        
        print "I'm going to init the graph"
        
        shape = self.inputProvider.outputs["Output"].shape
        print "data block shape: ", shape
        srcs    = []
        minMax = []
        
        print "* Data has shape=%r", (shape,)
        
        #create a layer for each channel of the input:
        
        slicer=OpMultiArraySlicer2(self.g)
        slicer.inputs["Input"].connect(self.inputProvider.outputs["Output"])
        slicer.inputs["AxisFlag"].setValue('c')
       
        
        
        
        nchannels = shape[-1]
        for ich in xrange(nchannels):

            if self._normalize_data:
                data=slicer.outputs['Slices'][ich][:].allocate().wait()
        
                #find the minimum and maximum value for normalization
                mm = (numpy.min(data), numpy.max(data))
                print "  - channel %d: min=%r, max=%r" % (ich, mm[0], mm[1])
                
                minMax.append(mm)
            else:
                minMax.append(None)
                
            layersrc = LazyflowSource(slicer.outputs['Slices'][ich], priority = 100)
            srcs.append(layersrc)
            
        #FIXME: we shouldn't merge channels automatically, but for now it's prettier
        layer1 = None
        if nchannels == 1:
            layer1 = GrayscaleLayer(srcs[0], thresholding=minMax[0])
            layer1.rangeRed   = minMax[0]
            print "  - showing raw data as grayscale"
        elif nchannels==2:
            layer1 = RGBALayer(red  = srcs[0], normalizeR=minMax[0],
                               green = srcs[1], normalizeG=minMax[1])
            layer1.rangeRed   = minMax[0]
            layer1.rangeGreen = minMax[1]
            print "  - showing channel 1 as red, channel 2 as green"
        elif nchannels==3:
            layer1 = RGBALayer(red   = srcs[0], normalizeR=minMax[0],
                               green = srcs[1], normalizeG=minMax[1],
                               blue  = srcs[2], normalizeB=minMax[2])
            layer1.rangeRed   = minMax[0]
            layer1.rangeGreen = minMax[1]
            layer1.rangeBlue  = minMax[2]
            print "  - showing channel 1 as red, channel 2 as green, channel 3 as blue"
        else:
            print "only 1,2 or 3 channels supported so far"
            return
        
        layer1.name = "Input data"
        layer1.ref_object = None
        self.layerstack.append(layer1)
        
        opImageList = OpArrayPiper(self.g)    
        opImageList.inputs["Input"].connect(self.inputProvider.outputs["Output"])
        
        #init the features operator
        opPF = OpPixelFeaturesPresmoothed(self.g)
        opPF.inputs["Input"].connect(self.readerOut.outputs["Output"])
        opPF.inputs["inputSigmas"].connect(self.readerSigma.outputs["Output"])
        opPF.inputs["Scales"].setValue(self.featScalesList)
        self.opPF=opPF
        
        print "####################################"
        
        #Caches the features
        opFeatureCache = OpBlockedArrayCache(self.g)
        #opSelCache = OpArrayCache(self.g)
        opFeatureCache.inputs["innerBlockShape"].setValue((1,8,8,8,16))
        opFeatureCache.inputs["outerBlockShape"].setValue((1,64,64,64,64))
        opFeatureCache.inputs["Input"].connect(opPF.outputs["Output"])
        opFeatureCache.inputs["fixAtCurrent"].setValue(False)  
        self.opFeatureCache=opFeatureCache
        
        
        self.initLabels()
        self.dataReadyToView.emit()
        
    def initLabels(self):
        #Add the layer to draw the labels, but don't add any labels
        shape=self.inputProvider.outputs["Output"].shape
        
        self.opLabels = OpBlockedSparseLabelArray(self.g)                                
        self.opLabels.inputs["shape"].setValue(shape[:-1] + (1,))
        self.opLabels.inputs["blockShape"].setValue((1, 32, 32, 32, 1))
        self.opLabels.inputs["eraser"].setValue(100)                
        
        self.labelsrc = LazyflowSinkSource(self.opLabels, self.opLabels.outputs["Output"], self.opLabels.inputs["Input"])
        transparent = QColor(0,0,0,0)
        self.labellayer = ColortableLayer(self.labelsrc, colorTable = [transparent.rgba()] )
        self.labellayer.name = "Labels"
        self.labellayer.ref_object = None
        self.layerstack.append(self.labellayer)    
    
    def initEditor(self):
        print "going to init editor"
        
        shape=self.inputProvider.outputs["Output"].shape
        
        self.editor = VolumeEditor(shape, self.layerstack, labelsink=self.labelsrc)
        #drawing will be enabled when the first label is added  
        self.editor.setDrawingEnabled(False)
        self.volumeEditorWidget.init(self.editor)
        model = self.editor.layerStack
        self.layerWidget.init(model)
        self.UpButton.clicked.connect(model.moveSelectedUp)
        model.canMoveSelectedUp.connect(self.UpButton.setEnabled)
        self.DownButton.clicked.connect(model.moveSelectedDown)
        model.canMoveSelectedDown.connect(self.DownButton.setEnabled)
        self.DeleteButton.clicked.connect(model.deleteSelected)
        model.canDeleteSelected.connect(self.DeleteButton.setEnabled)           
    
    def _createDefault16ColorColorTable(self):
        c = []
        c.append(QColor(0, 0, 255))
        c.append(QColor(255, 255, 0))
        c.append(QColor(255, 0, 0))
        c.append(QColor(0, 255, 0))
        c.append(QColor(0, 255, 255))
        c.append(QColor(255, 0, 255))
        c.append(QColor(255, 105, 180)) #hot pink
        c.append(QColor(102, 205, 170)) #dark aquamarine
        c.append(QColor(165,  42,  42)) #brown        
        c.append(QColor(0, 0, 128))     #navy
        c.append(QColor(255, 165, 0))   #orange
        c.append(QColor(173, 255,  47)) #green-yellow
        c.append(QColor(128,0, 128))    #purple
        c.append(QColor(192, 192, 192)) #silver
        c.append(QColor(240, 230, 140)) #khaki
        c.append(QColor(69, 69, 69))    # dark grey
        return c
    
    
    def onFeatureButtonClicked(self):
        self.StartClassificationButton.setEnabled(True)
        dlg=self.featureDlg
        dlg.show()
        
    
    def choosenDifferrentFeatSet(self):
        dlg=self.featureDlg
        
        selectedFeatures = dlg.featureTableWidget.createSelectedFeaturesBoolMatrix()
        print "******", selectedFeatures
        self.opPF.inputs['Matrix'].setValue(numpy.asarray(selectedFeatures))
    
    def initTheFeatureDlg(self):
        dlg = FeatureDlg()
        
        m = [[1,0,0,0],[1,0,0,0],[1,0,0,0],[1,0,0,0]]
        dlg.featureTableWidget.setSelectedFeatureBoolMatrix(m)
        
        self.featureDlg=dlg
        dlg.setWindowTitle("Features")
        dlg.createFeatureTable({"Features": [FeatureEntry("Gaussian smoothing"), FeatureEntry("Laplacian of Gaussian"), FeatureEntry("Hessian of Gaussian"), FeatureEntry("Hessian of Gaussian EV")]}, self.featScalesList)
        dlg.setImageToPreView((numpy.random.rand(100,100)*256).astype(numpy.uint8))
        dlg.accepted.connect(self.choosenDifferrentFeatSet)
    
app = QApplication(sys.argv)        
t = Main(sys.argv)
t.show()

app.exec_()
        
        
        
